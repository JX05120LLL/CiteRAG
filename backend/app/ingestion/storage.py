"""Private, opaque original-file storage; no browser path is ever accepted."""

import hashlib
import os
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from app.services.errors import ServiceError

MAX_FILE_BYTES = 20 * 1024 * 1024
SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}
_STORAGE_KEY = re.compile(r"[0-9a-f]{32}\.source")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


def validate_filename(filename: str) -> str:
    """Validate a display name, not a filesystem path."""
    if (
        not filename
        or len(filename) > 240
        or filename != filename.strip()
        or filename.endswith(".")
        or any(ord(character) < 32 or character in '/\\:<>|?*"' for character in filename)
        or filename.split(".", 1)[0].upper() in _RESERVED
        or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS
    ):
        raise ServiceError(422, "invalid_filename", "请使用有效的 TXT、MD、PDF 或 DOCX 文件名")
    return filename


@dataclass(frozen=True)
class StoredSource:
    storage_key: str
    sha256: str
    size: int
    filename: str


def _sync_directory(directory: Path) -> None:
    # Windows rename and file fsync are checked locally; it has no directory fsync API.
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


class PrivateSourceStore:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if root.is_symlink() or root.is_junction():
            raise ServiceError(503, "source_storage_unavailable", "原文存储目录不可用")
        self.root = root.resolve()

    def _check_root(self) -> None:
        if self.root.is_symlink() or self.root.is_junction() or self.root.resolve() != self.root:
            raise ServiceError(503, "source_storage_unavailable", "原文存储目录不可用")

    def path_for(self, storage_key: str) -> Path:
        self._check_root()
        if not _STORAGE_KEY.fullmatch(storage_key):
            raise ServiceError(422, "invalid_storage_key", "原文存储标识无效")
        path = self.root / storage_key
        if path.is_symlink() or path.is_junction() or path.resolve().parent != self.root:
            raise ServiceError(422, "invalid_storage_key", "原文存储标识无效")
        return path

    async def stage(self, filename: str, chunks: AsyncIterator[bytes]) -> StoredSource:
        filename = validate_filename(filename)
        storage_key = f"{uuid4().hex}.source"
        destination = self.path_for(storage_key)
        temporary = self.root / f"{uuid4().hex}.partial"
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        size = 0
        digest = hashlib.sha256()
        accepted = False
        try:
            with os.fdopen(descriptor, "wb") as stream:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > MAX_FILE_BYTES:
                        raise ServiceError(422, "file_too_large", "单个文件不能超过 20 MiB")
                    stream.write(chunk)
                    digest.update(chunk)
                if not size:
                    raise ServiceError(422, "empty_file", "文件为空，请选择有正文的资料")
                stream.flush()
                os.fsync(stream.fileno())
            self._check_root()
            os.replace(temporary, destination)
            _sync_directory(self.root)
            accepted = True
            return StoredSource(storage_key, digest.hexdigest(), size, filename)
        finally:
            temporary.unlink(missing_ok=True)
            if not accepted:
                destination.unlink(missing_ok=True)

    def discard(self, storage_key: str) -> None:
        self.path_for(storage_key).unlink(missing_ok=True)
        _sync_directory(self.root)

    def cleanup_orphans(self, referenced: set[str], *, older_than_seconds=86400) -> int:
        """Run under the sole API owner; never remove a committed or recent original."""
        self._check_root()
        cutoff = time.time() - older_than_seconds
        removed = 0
        for path in self.root.iterdir():
            if (
                path.name in referenced
                or not re.fullmatch(r"[0-9a-f]{32}\.(source|partial)", path.name)
                or path.is_symlink() or path.is_junction() or not path.is_file()
                or path.resolve().parent != self.root
                or path.stat().st_mtime >= cutoff
            ):
                continue
            path.unlink()
            removed += 1
        if removed:
            _sync_directory(self.root)
        return removed
