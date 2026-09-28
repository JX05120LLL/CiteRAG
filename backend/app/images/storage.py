"""Bounded private PNG/JPEG storage; provider input is re-encoded without metadata."""

import hashlib
import io
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.services.errors import ServiceError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
_KEY = re.compile(r"[0-9a-f]{32}\.image")


@dataclass(frozen=True)
class StoredImage:
    storage_key: str
    filename: str
    mime_type: str
    size: int
    sha256: str
    width: int
    height: int


def _filename(value: str) -> str:
    if (
        not value
        or len(value) > 240
        or value != value.strip()
        or any(ord(char) < 32 or char in '/\\:<>|?*"' for char in value)
        or Path(value).suffix.lower() not in {".jpg", ".jpeg", ".png"}
    ):
        raise ServiceError(422, "invalid_image", "请选择 PNG 或 JPEG 图片")
    return value


def _sanitize(path: Path, filename: str) -> tuple[bytes, str, int, int]:
    try:
        with Image.open(path) as probe:
            image_format = probe.format
            width, height = probe.size
            if (
                image_format not in {"PNG", "JPEG"}
                or getattr(probe, "n_frames", 1) != 1
                or width <= 10
                or height <= 10
                or width * height > MAX_IMAGE_PIXELS
                or max(width, height) > 200 * min(width, height)
            ):
                raise ValueError
            if (Path(filename).suffix.lower() == ".png") != (image_format == "PNG"):
                raise ValueError
            probe.verify()
        with Image.open(path) as decoded:
            image = ImageOps.exif_transpose(decoded)
            image.load()
            if image_format == "JPEG":
                image = image.convert("RGB")
            elif image.mode not in {"RGB", "RGBA", "L", "LA"}:
                image = image.convert("RGBA")
            output = io.BytesIO()
            image.save(output, format=image_format)
        data = output.getvalue()
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError
        mime = "image/png" if image_format == "PNG" else "image/jpeg"
        return data, mime, image.width, image.height
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ServiceError(422, "invalid_image", "图片损坏、格式不符或尺寸超过限制") from None


class PrivateImageStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or root.is_junction():
            raise ServiceError(503, "image_storage_unavailable", "图片存储目录不可用")
        self.root = root.resolve()

    def _check_root(self) -> None:
        if self.root.is_symlink() or self.root.is_junction() or self.root.resolve() != self.root:
            raise ServiceError(503, "image_storage_unavailable", "图片存储目录不可用")

    def path_for(self, key: str) -> Path:
        self._check_root()
        if not _KEY.fullmatch(key):
            raise ServiceError(422, "invalid_image_key", "图片标识无效")
        path = self.root / key
        if path.is_symlink() or path.is_junction() or path.resolve().parent != self.root:
            raise ServiceError(422, "invalid_image_key", "图片标识无效")
        return path

    async def stage(self, filename: str, chunks: AsyncIterator[bytes]) -> StoredImage:
        filename = _filename(filename)
        temporary = self.root / f"{uuid4().hex}.partial"
        key = f"{uuid4().hex}.image"
        destination = self.path_for(key)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        raw_size = 0
        accepted = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                async for chunk in chunks:
                    raw_size += len(chunk)
                    if raw_size > MAX_IMAGE_BYTES:
                        raise ServiceError(422, "image_too_large", "每张图片最多 10 MiB")
                    stream.write(chunk)
                if raw_size == 0:
                    raise ServiceError(422, "invalid_image", "图片为空")
                stream.flush()
                os.fsync(stream.fileno())
            data, mime, width, height = _sanitize(temporary, filename)
            with destination.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            accepted = True
            return StoredImage(
                key, filename, mime, len(data), hashlib.sha256(data).hexdigest(), width, height
            )
        finally:
            temporary.unlink(missing_ok=True)
            if not accepted:
                destination.unlink(missing_ok=True)

    def read(self, key: str) -> bytes:
        path = self.path_for(key)
        try:
            data = path.read_bytes()
        except OSError:
            raise ServiceError(503, "image_storage_unavailable", "图片暂不可读取") from None
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ServiceError(503, "image_storage_unavailable", "图片内容无效")
        return data

    def discard(self, key: str) -> None:
        self.path_for(key).unlink(missing_ok=True)

    def cleanup_orphans(self, referenced: set[str], *, older_than_seconds: int = 86400) -> int:
        self._check_root()
        cutoff = time.time() - older_than_seconds
        removed = 0
        for path in self.root.iterdir():
            if (
                path.name in referenced
                or not re.fullmatch(r"[0-9a-f]{32}\.(image|partial)", path.name)
                or path.is_symlink()
                or path.is_junction()
                or not path.is_file()
                or path.resolve().parent != self.root
                or path.stat().st_mtime >= cutoff
            ):
                continue
            path.unlink()
            removed += 1
        return removed
