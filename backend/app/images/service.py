"""Owner-bound image attachments and strictly labelled model observations."""

import json
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.images.storage import PrivateImageStore, StoredImage
from app.models import Conversation, ImageAttachment, KnowledgeBase, MessageImage
from app.providers.errors import ProviderError
from app.services.conversation_retention import active_conversation
from app.services.errors import ServiceError

PENDING_TTL = timedelta(hours=24)
IMAGE_TTL = timedelta(days=30)


class ImageObserver(Protocol):
    async def observe_images(self, images: list[tuple[str, bytes]]): ...


def image_view(image: ImageAttachment) -> dict:
    return {
        "id": image.id,
        "filename": image.filename,
        "mime_type": image.mime_type,
        "width": image.width,
        "height": image.height,
        "size": image.size,
        "observation": image.observation if image.observation_status == "ready" else None,
        "observation_status": image.observation_status,
        "needs_confirmation": image.needs_confirmation,
        "confirmed_identifier": image.confirmed_identifier,
        "expires_at": image.expires_at,
    }


def checked_observation(raw: str) -> tuple[str, bool]:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"observation", "uncertain_identifiers"}:
            raise ValueError
        text = data["observation"]
        uncertain = data["uncertain_identifiers"]
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 1000
            or not isinstance(uncertain, list)
            or len(uncertain) > 5
            or any(
                not isinstance(value, str) or not value or len(value) > 80 for value in uncertain
            )
        ):
            raise ValueError
        return text.strip(), bool(uncertain)
    except (ValueError, TypeError):
        raise ServiceError(503, "image_observation_invalid", "图片观察结果无效，请重试") from None


class ImageService:
    def __init__(self, session: AsyncSession, store: PrivateImageStore):
        self.session = session
        self.store = store

    async def _owned_chat(self, owner: UUID, conversation_id: UUID, *, lock=False):
        query = (
            select(Conversation, KnowledgeBase)
            .outerjoin(
                KnowledgeBase,
                Conversation.kb_id == KnowledgeBase.id,
            )
            .where(
                Conversation.id == conversation_id,
                Conversation.owner_id == owner,
                or_(Conversation.kb_id.is_(None), KnowledgeBase.owner_id == owner),
                active_conversation(),
            )
        )
        row = (await self.session.execute(query.with_for_update(of=Conversation)
                                          if lock else query)).first()
        if row is None:
            raise ServiceError(404, "conversation_not_found", "聊天不存在或不可访问")
        if lock and row[1] is not None:
            await self.session.get(KnowledgeBase, row[1].id, with_for_update=True)
        return row

    async def accept(self, owner: UUID, conversation_id: UUID, stored: StoredImage) -> dict:
        conversation, kb = await self._owned_chat(owner, conversation_id, lock=True)
        if conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档，请恢复后再上传图片")
        if kb is not None and kb.status != "ready":
            raise ServiceError(409, "kb_not_ready", "知识库未就绪，暂不能上传图片")
        image = ImageAttachment(
            owner_id=owner,
            conversation_id=conversation_id,
            storage_key=stored.storage_key,
            filename=stored.filename,
            mime_type=stored.mime_type,
            size=stored.size,
            sha256=stored.sha256,
            width=stored.width,
            height=stored.height,
            observation_status="pending",
            expires_at=datetime.now(UTC) + PENDING_TTL,
        )
        self.session.add(image)
        await self.session.commit()
        return image_view(image)

    async def owned(
        self, owner: UUID, conversation_id: UUID, image_id: UUID, *, writable: bool = False
    ) -> ImageAttachment:
        conversation, _ = await self._owned_chat(owner, conversation_id)
        if writable and conversation.archived_at is not None:
            raise ServiceError(409, "conversation_archived", "聊天已归档，请恢复后再修改图片")
        image = await self.session.scalar(
            select(ImageAttachment).where(
                ImageAttachment.id == image_id,
                ImageAttachment.owner_id == owner,
                ImageAttachment.conversation_id == conversation_id,
                ImageAttachment.expires_at > datetime.now(UTC),
            )
        )
        if image is None:
            raise ServiceError(404, "image_not_found", "图片不存在或已过期")
        return image

    async def bind(
        self, owner: UUID, conversation_id: UUID, message_id: UUID, image_ids: list[UUID]
    ) -> None:
        if len(image_ids) > 2 or len(set(image_ids)) != len(image_ids):
            raise ServiceError(422, "invalid_images", "每条问题最多绑定两张不同图片")
        if not image_ids:
            return
        rows = list(
            await self.session.scalars(
                select(ImageAttachment)
                .where(
                    ImageAttachment.id.in_(image_ids),
                    ImageAttachment.owner_id == owner,
                    ImageAttachment.conversation_id == conversation_id,
                    ImageAttachment.expires_at > datetime.now(UTC),
                )
                .with_for_update()
            )
        )
        if len(rows) != len(image_ids):
            raise ServiceError(404, "image_not_found", "图片不存在、已过期或不属于当前聊天")
        for image in rows:
            self.session.add(MessageImage(message_id=message_id, attachment_id=image.id))
            image.expires_at = max(image.expires_at, image.created_at + IMAGE_TTL)

    async def for_message(self, message_id: UUID) -> list[ImageAttachment]:
        return list(
            await self.session.scalars(
                select(ImageAttachment)
                .join(
                    MessageImage,
                    MessageImage.attachment_id == ImageAttachment.id,
                )
                .where(MessageImage.message_id == message_id)
                .order_by(ImageAttachment.created_at, ImageAttachment.id)
            )
        )

    async def observe(self, message_id: UUID, observer: ImageObserver) -> tuple[str, bool]:
        rows = await self.for_message(message_id)
        observations = []
        uncertain = False
        for image in rows:
            if image.expires_at <= datetime.now(UTC):
                raise ServiceError(410, "image_expired", "图片已过期，请重新上传")
            if image.observation_status != "ready":
                try:
                    result = await observer.observe_images(
                        [(image.mime_type, self.store.read(image.storage_key))]
                    )
                    observed, unsure = checked_observation(result.content)
                except (ProviderError, ServiceError, OSError, ValueError) as error:
                    image.observation_status = "failed"
                    await self.session.commit()
                    if isinstance(error, ProviderError):
                        category = error.category if error.category in {
                            "authentication", "permission", "quota", "rate_limit",
                            "request", "upstream", "timeout", "network", "protocol",
                            "output_limit",
                        } else "unavailable"
                        raise ServiceError(503, f"image_provider_{category}",
                                           "图片模型调用失败，请查看错误代码") from None
                    if isinstance(error, ServiceError):
                        raise error from None
                    raise ServiceError(503, "image_storage_unavailable",
                                       "图片原文件读取失败，请重新上传") from None
                image.observation = observed
                image.observation_status = "ready"
                image.needs_confirmation = unsure
                image.observation_model = result.model
                await self.session.commit()
            else:
                observed = image.observation or ""
                unsure = image.needs_confirmation
            observations.append(
                observed
                + (
                    f"\n用户确认的编号：{image.confirmed_identifier}"
                    if image.confirmed_identifier
                    else ""
                )
            )
            uncertain = uncertain or (unsure and image.confirmed_identifier is None)
        return "\n".join(
            f"图片{index}观察：{text}" for index, text in enumerate(observations, 1)
        ), uncertain

    async def confirm(
        self, owner: UUID, conversation_id: UUID, image_id: UUID, identifier: str
    ) -> dict:
        image = await self.owned(owner, conversation_id, image_id, writable=True)
        if not image.needs_confirmation:
            raise ServiceError(409, "image_confirmation_not_needed", "该图片无需确认编号")
        image.confirmed_identifier = identifier
        await self.session.commit()
        return image_view(image)

    async def remove_pending(self, owner: UUID, conversation_id: UUID, image_id: UUID) -> None:
        image = await self.owned(owner, conversation_id, image_id, writable=True)
        bound = await self.session.scalar(
            select(MessageImage.message_id)
            .where(
                MessageImage.attachment_id == image_id,
            )
            .limit(1)
        )
        if bound is not None:
            raise ServiceError(409, "image_in_use", "图片已用于聊天记录，不能单独删除")
        key = image.storage_key
        await self.session.delete(image)
        await self.session.commit()
        self.store.discard(key)
