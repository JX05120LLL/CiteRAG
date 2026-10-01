import os
import signal
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import case, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.api.agent import capability_router as agent_capability_router
from app.api.agent import router as agent_router
from app.api.answers import router as answers_router
from app.api.boundaries import require_local_request
from app.api.documents import router as documents_router
from app.api.images import router as images_router
from app.api.routes import router
from app.api.tools import router as tools_router
from app.api.voice import router as voice_router
from app.config import LOCAL_RUNTIME_ROOT, Settings
from app.database import Database
from app.images.storage import PrivateImageStore
from app.ingestion.jobs import IngestionRunner
from app.ingestion.storage import PrivateSourceStore
from app.maintenance.gate import BackupGate
from app.maintenance.runner import DailyBackupRunner
from app.models import AnswerAttempt, Conversation, KnowledgeBase, LocalProfile, ToolCall
from app.rag.engine import assert_isolated_configuration
from app.rag.ingestion_adapter import LightRAGIngestionAdapter
from app.rag.owner import ApiOwner, OwnerLost
from app.rag.runtime import RagRuntime
from app.services.conversation_retention import RetentionRunner
from app.services.errors import ServiceError
from app.tools.gateway import built_in_tools
from app.voice.runtime import VoiceRuntime


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
            async def workspace_resolver(kb_id):
                async with db.sessions() as session:
                    workspace = await session.scalar(select(KnowledgeBase.active_workspace).where(
                        KnowledgeBase.id == kb_id, KnowledgeBase.owner_id.is_not(None),
                    ))
                    if workspace is None:
                        raise ServiceError(404, "kb_not_found", "知识库不存在或不可访问")
                    return workspace

            runtime = RagRuntime(settings, workspace_resolver=workspace_resolver)
            application.state.rag_runtime = runtime
            stack.push_async_callback(runtime.close)
            if application.state.owner is not None:
                await runtime.start(application.state.owner.assert_owned)
            application.state.rag_probe = runtime.probe
            if lifespan_hook is not None:
                await stack.enter_async_context(lifespan_hook(application))
            if db is not None:
                store = PrivateSourceStore(application.state.source_root)
                application.state.source_store = store
                application.state.image_store = PrivateImageStore(application.state.image_root)
                adapter = application.state.ingestion_adapter or LightRAGIngestionAdapter(runtime)
                runner = IngestionRunner(
                    db, store, adapter, application.state.owner.assert_owned,
                    enabled=settings.ingestion_enabled,
                    backup_gate=application.state.backup_gate,
                )
                application.state.ingestion_runner = runner
                stack.push_async_callback(runner.close)
                await runner.start()
                # A dead process cannot complete an old answer. Never replay a
                # provider request solely because its durable attempt was running.
                async with db.sessions() as session:
                    owned = select(Conversation.id).where(
                        Conversation.owner_id == await session.scalar(select(LocalProfile.id)),
                    )
                    await session.execute(update(AnswerAttempt).where(
                        AnswerAttempt.conversation_id.in_(owned),
                        AnswerAttempt.status == "running",
                    ).values(status="interrupted", error_code="server_restarted",
                             finished_at=datetime.now(UTC)))
                    await session.execute(update(ToolCall).where(
                        ToolCall.status == "running",
                    ).values(status=case((ToolCall.effect == "write", "unknown"),
                                         else_="interrupted"), error_code="server_restarted",
                             finished_at=datetime.now(UTC)))
                    from app.agent.repository import recover_running
                    await recover_running(session)
                    await session.commit()
                if settings.agent_enabled:
                    from app.agent.checkpoints import checkpoint_store
                    from app.agent.runner import AgentRunner
                    saver = await stack.enter_async_context(checkpoint_store(db))
                    application.state.agent_runtime = AgentRunner(application, saver)
                    stack.push_async_callback(application.state.agent_runtime.close)
                    await application.state.agent_runtime.start_monitor()
                retention = RetentionRunner(db, application.state.owner.assert_owned,
                                            application.state.backup_gate,
                                            application.state.image_store)
                stack.push_async_callback(retention.close)
                await retention.start()
                application.state.retention_runner = retention
                stack.push_async_callback(application.state.voice_runtime.close)
                await application.state.voice_runtime.start()
                if settings.backup_enabled:
                    backup = DailyBackupRunner(
                        settings, runtime, application.state.owner.assert_owned,
                        application.state.backup_gate,
                    )
                    stack.push_async_callback(backup.close)
                    await backup.start()
                    application.state.backup_runner = backup
            yield
            application.state.owner = None

    application = FastAPI(title="CiteRAG API", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.database = database or Database.from_settings(settings)
    application.state.owner = None
    application.state.rag_runtime = None
    application.state.rag_probe = None
    application.state.source_root = LOCAL_RUNTIME_ROOT / "sources"
    application.state.image_root = LOCAL_RUNTIME_ROOT / "attachments"
    application.state.source_store = None
    application.state.image_store = None
    application.state.image_observer = None
    application.state.ingestion_runner = None
    application.state.retention_runner = None
    application.state.ingestion_adapter = None
    application.state.backup_gate = BackupGate()
    application.state.backup_runner = None
    application.state.answer_enabled = settings.answer_enabled
    application.state.agent_enabled = settings.agent_enabled
    application.state.agent_runtime = None
    application.state.query_adapter = None
    application.state.answer_adapter = None
    application.state.tool_registry = built_in_tools()
    if settings.mcp_enabled:
        from app.tools.mcp import load_reviewed_registry
        application.state.tool_registry.update(load_reviewed_registry(
            LOCAL_RUNTIME_ROOT / "tools" / "registry.json"))
    application.state.voice_runtime = VoiceRuntime(application)

    @application.middleware("http")
    async def request_boundaries(request: Request, call_next):
        admitted = False
        try:
            require_local_request(request, settings)
            owner = application.state.owner
            if application.state.database is not None:
                if owner is None:
                    raise OwnerLost("Application startup has not completed")
                owner.assert_owned()
            if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                await application.state.backup_gate.enter()
                admitted = True
            response = await call_next(request)
            if admitted:
                await application.state.voice_runtime.reconcile()
                if application.state.agent_runtime is not None:
                    await application.state.agent_runtime.reconcile()
            if owner is not None:
                owner.assert_owned()
        except OwnerLost:
            response = error_response(503, "owner_unavailable", "服务所有权已丢失，请稍后重试")
        except ServiceError as error:
            response = error_response(error.status, error.code, error.message)
        except BaseException:
            if admitted:
                await application.state.backup_gate.leave()
            raise
        if admitted:
            iterator = getattr(response, "body_iterator", None)
            if iterator is None:
                await application.state.backup_gate.leave()
            else:
                async def gated_body():
                    try:
                        async for part in iterator:
                            yield part
                    finally:
                        await application.state.backup_gate.leave()

                response.body_iterator = gated_body()
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
    application.include_router(documents_router)
    application.include_router(images_router)
    application.include_router(answers_router)
    application.include_router(agent_router)
    application.include_router(agent_capability_router)
    application.include_router(tools_router)
    application.include_router(voice_router)
    return application


app = create_app()
