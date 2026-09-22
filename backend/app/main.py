import os
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.api.boundaries import require_local_request
from app.api.routes import router
from app.config import Settings
from app.database import Database
from app.rag.engine import assert_isolated_configuration
from app.rag.owner import ApiOwner, OwnerLost
from app.services.errors import ServiceError


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": {"code": code, "message": message}})


def stop_process_on_owner_loss() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def create_app(
    settings: Settings | None = None,
    *,
    database: Database | None = None,
    lifespan_hook: Callable[[FastAPI], AbstractAsyncContextManager] | None = None,
    on_owner_lost: Callable[[], None] = stop_process_on_owner_loss,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        assert_isolated_configuration(os.environ, Path.cwd())
        async with AsyncExitStack() as stack:
            db = application.state.database
            if db is not None:
                stack.push_async_callback(db.engine.dispose)
                if settings.database_url is None:
                    raise RuntimeError(
                        "Database injection requires explicit PostgreSQL configuration"
                    )
                application.state.owner = await stack.enter_async_context(
                    ApiOwner(settings.database_url.get_secret_value(), on_lost=on_owner_lost)
                )
                await db.verify_schema()
            if lifespan_hook is not None:
                await stack.enter_async_context(lifespan_hook(application))
            yield
            application.state.owner = None

    application = FastAPI(title="CiteRAG API", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.database = database or Database.from_settings(settings)
    application.state.owner = None

    @application.middleware("http")
    async def request_boundaries(request: Request, call_next):
        try:
            require_local_request(request, settings)
            owner = application.state.owner
            if application.state.database is not None:
                if owner is None:
                    raise OwnerLost("Application startup has not completed")
                owner.assert_owned()
            response = await call_next(request)
            if owner is not None:
                owner.assert_owned()
        except OwnerLost:
            response = error_response(503, "owner_unavailable", "服务所有权已丢失，请稍后重试")
        except ServiceError as error:
            response = error_response(error.status, error.code, error.message)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @application.exception_handler(ServiceError)
    async def service_error(request: Request, error: ServiceError):
        return error_response(error.status, error.code, error.message)

    @application.exception_handler(SQLAlchemyError)
    async def persistence_error(request: Request, error: SQLAlchemyError):
        # Do not return/log the exception: driver details can contain credentials
        # or bind parameters, and a failed write must never look accepted.
        return error_response(503, "persistence_failed", "业务数据库不可用，操作未确认保存")

    @application.exception_handler(RequestValidationError)
    async def validation_error(request: Request, error: RequestValidationError):
        # Do not echo rejected input, which may include private local content.
        return error_response(422, "validation_error", "请求参数不符合要求")

    application.include_router(router)
    return application


app = create_app()
