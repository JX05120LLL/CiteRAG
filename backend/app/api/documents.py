"""Private file access and durable ingestion HTTP contracts."""
import asyncio
import hashlib
from datetime import datetime
from typing import Literal
from unicodedata import category
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.dependencies import LocalOwner, Session
from app.models import Document, IngestionJob
from app.services.documents import DocumentService, owned_job, owned_kb
from app.services.errors import ServiceError

router = APIRouter(prefix="/api")
MAX_BATCH_BYTES = 5 * 20 * 1024 * 1024 + 64 * 1024


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_request_id: UUID


def job_view(job, active_workspace=None):
    current = active_workspace is None or not job.engine_mutated or (
        (job.target_workspace if job.operation == "upload" else job.retired_workspace)
        == active_workspace
    )
    return {
        "id": job.id, "kb_id": job.kb_id, "operation": job.operation,
        "status": job.status, "stage": job.stage, "error_code": job.error_code,
        "message": job.message, "document_ids": job.document_ids,
        "created_at": job.created_at, "engine_mutated": job.engine_mutated,
        "can_retry": job.status in {"failed", "interrupted"}
        and job.error_code not in {"rebuild_required", "stale_job"} and current,
        "cleanup_pending": job.cleanup_pending,
    }


class DocumentView(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    size: int
    status: Literal["pending", "parsing", "parsed", "indexing", "ready", "failed"]
    error_code: str | None
    # Keeping the view explicit prevents exposing storage paths or engine namespaces.
    created_at: datetime
    doc_code: str | None
    model_code: str | None
    edition: str | None


class DocumentAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    doc_code: str | None
    model_code: str | None
    edition: str | None

    @field_validator("doc_code", "model_code", "edition")
    @classmethod
    def valid_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if (not cleaned or len(cleaned) > 80
            or any(category(char).startswith("C") for char in cleaned)):
            raise ValueError("Document attribute must be printable and at most 80 characters")
        return cleaned


def runner_for(request):
    runner = request.app.state.ingestion_runner
    if runner is None or not runner.available:
        raise ServiceError(503, "ingestion_unavailable", "资料任务服务不可用，请检查服务状态")
    return runner


@router.post("/knowledge-bases/{kb_id}/documents", status_code=202)
async def upload_documents(kb_id: UUID, request: Request, owner: LocalOwner, session: Session):
    runner = runner_for(request)
    await owned_kb(session, owner, kb_id)
    await session.rollback()
    store = request.app.state.source_store
    received = 0

    async def limited_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_BATCH_BYTES:
            raise MultiPartException("Upload exceeds batch limit")
        return message

    bounded = Request(request.scope, receive=limited_receive)
    sources = []
    try:
        async with bounded.form(max_files=5, max_fields=1, max_part_size=1024) as form:
            keys = set(form.keys())
            files = form.getlist("files")
            if keys != {"files", "client_request_id"} or not 1 <= len(files) <= 5:
                raise ServiceError(422, "invalid_file", "每批上传 1 至 5 份文件，并提供请求键")
            try:
                request_id = UUID(str(form["client_request_id"]))
            except (ValueError, TypeError):
                raise ServiceError(422, "validation_error", "上传请求键不符合要求") from None
            for file in files:
                if not isinstance(file, UploadFile) or not file.filename:
                    raise ServiceError(422, "invalid_file", "上传文件格式不符合要求")

                async def chunks(upload=file):
                    while block := await upload.read(1024 * 1024):
                        yield block

                try:
                    sources.append(await store.stage(file.filename, chunks()))
                except OSError:
                    raise ServiceError(
                        503, "storage_unavailable", "原文未确认可靠保存，请检查本机存储后重试",
                    ) from None
        job, created = await DocumentService(session).accept(owner, kb_id, request_id, sources)
    except (HTTPException, MultiPartException):
        for source in sources:
            store.discard(source.storage_key)
        raise ServiceError(422, "invalid_file", "文件数量、大小或上传格式不符合要求") from None
    except ServiceError:
        for source in sources:
            store.discard(source.storage_key)
        raise
    # Unknown commit outcome keeps files for conservative orphan recovery, never deletes
    # a possibly committed original. They remain private and are not reported accepted.
    if not created:
        for source in sources:
            store.discard(source.storage_key)
    runner.wake()
    return job_view(job)


@router.get("/knowledge-bases/{kb_id}/documents")
async def list_documents(kb_id: UUID, owner: LocalOwner, session: Session):
    await owned_kb(session, owner, kb_id)
    items = await session.scalars(select(Document).where(Document.kb_id == kb_id).order_by(
        Document.created_at, Document.id,
    ))
    return {"items": [DocumentView.model_validate(item) for item in items]}


@router.patch("/documents/{document_id}/attributes", response_model=DocumentView)
async def set_document_attributes(
    document_id: UUID, body: DocumentAttributes, owner: LocalOwner, session: Session,
):
    document = await session.get(Document, document_id)
    if document is None:
        raise ServiceError(404, "document_not_found", "资料不存在或不可访问")
    kb = await owned_kb(session, owner, document.kb_id, lock=True)
    document = await session.get(Document, document_id, with_for_update=True,
                                 populate_existing=True)
    if kb.status != "ready" or document.status != "ready":
        raise ServiceError(409, "kb_not_ready", "资料未入库就绪，暂不能确认精确属性")
    values = body.model_dump()
    if any(getattr(document, field) != value for field, value in values.items()):
        for field, value in values.items():
            setattr(document, field, value)
        kb.revision += 1
    await session.commit()
    return document


@router.get("/knowledge-bases/{kb_id}/jobs")
async def list_jobs(kb_id: UUID, owner: LocalOwner, session: Session):
    kb = await owned_kb(session, owner, kb_id)
    jobs = await session.scalars(select(IngestionJob).where(IngestionJob.kb_id == kb_id).order_by(
        IngestionJob.created_at.desc(), IngestionJob.id,
    ).limit(100))
    return {"items": [job_view(job, kb.active_workspace) for job in jobs]}


@router.get("/jobs/{job_id}")
async def get_job(job_id: UUID, owner: LocalOwner, session: Session):
    job = await owned_job(session, owner, job_id)
    kb = await owned_kb(session, owner, job.kb_id)
    return job_view(job, kb.active_workspace)


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry_job(job_id: UUID, request: Request, owner: LocalOwner, session: Session):
    runner = runner_for(request)
    job = await DocumentService(session).retry(owner, job_id)
    runner.wake()
    return job_view(job)


@router.post("/knowledge-bases/{kb_id}/rebuild", status_code=202)
async def rebuild_kb(
    kb_id: UUID, body: RebuildRequest, request: Request, owner: LocalOwner, session: Session,
):
    runner = runner_for(request)
    job = await DocumentService(session).rebuild(owner, kb_id, body.client_request_id)
    runner.wake()
    return job_view(job)


@router.get("/documents/{document_id}/blocks")
async def parsed_blocks(document_id: UUID, owner: LocalOwner, session: Session):
    blocks = await DocumentService(session).blocks(owner, document_id)
    return {"items": [{"ordinal": block.ordinal, "text": block.text, "locator": block.locator,
                       "start": block.start, "end": block.end} for block in blocks]}


@router.get("/documents/{document_id}/original")
async def original(document_id: UUID, request: Request, owner: LocalOwner, session: Session):
    document = await DocumentService(session).readable(owner, document_id)
    store = request.app.state.source_store

    def read_bounded():
        with store.path_for(document.storage_key).open("rb") as stream:
            return stream.read(document.size + 1)

    try:
        data = await asyncio.to_thread(read_bounded)
    except OSError:
        raise ServiceError(409, "source_missing", "原文缺失，请检查备份") from None
    if len(data) != document.size or hashlib.sha256(data).hexdigest() != document.sha256:
        raise ServiceError(409, "source_changed", "原文校验不一致，请检查备份")
    # A disk read yields to maintenance. Discard cached ORM state and check again
    # before returning private content; no stale ready state may authorize output.
    session.expire_all()
    document = await DocumentService(session).readable(owner, document_id)
    return Response(data, media_type="application/octet-stream", headers={
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(document.filename),
        "Content-Security-Policy": "sandbox; default-src 'none'",
    })
