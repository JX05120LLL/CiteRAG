"""Private image input for the current conversation, never static file hosting."""

from unicodedata import category
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, field_validator
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException

from app.api.dependencies import LocalOwner, Session
from app.images.service import ImageService
from app.services.errors import ServiceError

router = APIRouter(prefix="/api")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024 + 64 * 1024


class ConfirmImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str

    @field_validator("identifier")
    @classmethod
    def printable(cls, value: str) -> str:
        value = value.strip()
        if not value or len(value) > 80 or any(category(char).startswith("C") for char in value):
            raise ValueError("Confirmed identifier must be printable and at most 80 characters")
        return value


def _service(request: Request, session: Session) -> ImageService:
    store = request.app.state.image_store
    if store is None:
        raise ServiceError(503, "image_storage_unavailable", "图片服务暂不可用")
    return ImageService(session, store)


@router.post("/conversations/{conversation_id}/attachments", status_code=201)
async def upload_image(
    conversation_id: UUID, request: Request, owner: LocalOwner, session: Session
):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    if not request.app.state.answer_enabled:
        raise ServiceError(503, "answer_disabled", "图片提问尚未启用")
    service = _service(request, session)
    await service._owned_chat(owner, conversation_id)
    await session.rollback()
    received = 0

    async def limited_receive():
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))
        if received > MAX_UPLOAD_BYTES:
            raise MultiPartException("Image upload exceeds limit")
        return message

    bounded = Request(request.scope, receive=limited_receive)
    try:
        async with bounded.form(max_files=1, max_fields=0, max_part_size=1024) as form:
            if set(form.keys()) != {"file"} or not isinstance(form["file"], UploadFile):
                raise ServiceError(422, "invalid_image", "每次只能上传一张 PNG 或 JPEG 图片")
            file = form["file"]
            if not file.filename:
                raise ServiceError(422, "invalid_image", "图片文件名无效")

            async def chunks():
                while block := await file.read(1024 * 1024):
                    yield block

            stored = await service.store.stage(file.filename, chunks())
    except (HTTPException, MultiPartException):
        raise ServiceError(422, "invalid_image", "图片大小或上传格式不符合要求") from None
    try:
        return await service.accept(owner, conversation_id, stored)
    except ServiceError:
        service.store.discard(stored.storage_key)
        raise
    # Unknown commit outcomes retain private bytes for conservative orphan recovery.


@router.get("/conversations/{conversation_id}/attachments/{image_id}")
async def get_image(
    conversation_id: UUID, image_id: UUID, request: Request, owner: LocalOwner, session: Session
):
    service = _service(request, session)
    image = await service.owned(owner, conversation_id, image_id)
    return Response(
        service.store.read(image.storage_key),
        media_type=image.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/conversations/{conversation_id}/attachments/{image_id}", status_code=204)
async def delete_pending_image(
    conversation_id: UUID, image_id: UUID, request: Request, owner: LocalOwner, session: Session
):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    await _service(request, session).remove_pending(owner, conversation_id, image_id)
    return Response(status_code=204)


@router.post("/conversations/{conversation_id}/attachments/{image_id}/confirm")
async def confirm_image(
    conversation_id: UUID,
    image_id: UUID,
    body: ConfirmImage,
    request: Request,
    owner: LocalOwner,
    session: Session,
):
    request.app.state.voice_runtime.registry.require_text(conversation_id)
    return await _service(request, session).confirm(
        owner, conversation_id, image_id, body.identifier
    )
