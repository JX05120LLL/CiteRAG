"""Local, non-secret evidence for explicit model and RAG validation runs."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)

from app.credentials import DashScopeConfig, _current_user_sid

type CapabilityState = Literal[
    "not_configured", "unverified", "available", "unavailable"
]
type ValidationKind = Literal["models", "rag"]
type ValidationOutcome = Literal["available", "unavailable"]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmptyString = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]

_REPORT_NAMES: dict[ValidationKind, str] = {
    "models": "models.json",
    "rag": "rag.json",
}
_MAX_REPORT_BYTES = 262_144


class ValidationUsage(BaseModel):
    """Provider usage counters; prices and request content are deliberately excluded."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)


class StorageRowCount(BaseModel):
    """A generated workspace and allow-listed LightRAG table count; no row content."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    table: Annotated[str, StringConstraints(pattern=r"^lightrag_[a-z0-9_]{1,54}$")]
    workspace: Annotated[str, StringConstraints(pattern=r"^kb_[0-9a-f]{32}$")]
    row_count: int = Field(ge=0)


class ValidationReport(BaseModel):
    """The complete allow-list of evidence that may be persisted locally."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    kind: ValidationKind
    fingerprint: Sha256Digest
    outcome: ValidationOutcome
    verified_at: AwareDatetime
    provider: NonEmptyString | None = None
    region: NonEmptyString | None = None
    model_ids: dict[NonEmptyString, NonEmptyString] = Field(default_factory=dict)
    embedding_dimension: int | None = Field(default=None, ge=1, le=65_536)
    request_ids: tuple[NonEmptyString, ...] = ()
    elapsed_ms: int = Field(ge=0)
    usage: dict[NonEmptyString, ValidationUsage] = Field(default_factory=dict)
    failure_category: NonEmptyString | None = None
    boundary_category: Literal["input_limit"] | None = None
    image_digest: NonEmptyString | None = None
    lightrag_commit: NonEmptyString | None = None
    storage_names: tuple[NonEmptyString, ...] = ()
    storage_counts: tuple[StorageRowCount, ...] = ()
    postgresql_major: int | None = Field(default=None, ge=1)
    vector_version: NonEmptyString | None = None
    source_verified: bool | None = None
    deletion_verified: bool | None = None
    restart_verified: bool | None = None
    cleanup_verified: bool | None = None

    @field_validator("verified_at")
    @classmethod
    def utc_timestamp_only(cls, value: datetime) -> datetime:
        if value.utcoffset() != UTC.utcoffset(value):
            raise ValueError("validation timestamp must be UTC")
        return value


def capability_state(
    *, configured: bool, report: ValidationReport | None
) -> CapabilityState:
    if not configured:
        return "not_configured"
    if report is None:
        return "unverified"
    return report.outcome


def _canonical_json(value: object) -> bytes:
    def reject_secret(candidate: object) -> object:
        if isinstance(candidate, SecretStr):
            raise ValueError("secrets cannot be fingerprint inputs")
        if isinstance(candidate, Mapping):
            return {str(key): reject_secret(item) for key, item in candidate.items()}
        if isinstance(candidate, (tuple, list)):
            return [reject_secret(item) for item in candidate]
        if candidate is None or isinstance(candidate, (str, int, float, bool)):
            return candidate
        raise TypeError(f"unsupported fingerprint value: {type(candidate).__name__}")

    public_value = reject_secret(value)
    return json.dumps(
        public_value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _fingerprint(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _require_sha256(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("credential ciphertext hash must be lowercase SHA-256")
    return value


def model_fingerprint(config: DashScopeConfig, credential_ciphertext_hash: str) -> str:
    """Hash every model setting that changes the meaning of validation evidence."""

    return _fingerprint(
        {
            # v2 requires endpoint-specific request ID semantics. Old reports
            # lack ID provenance and must not become current evidence again.
            "schema": 2,
            "provider": "dashscope",
            "region": config.region,
            "workspace_id": config.workspace_id,
            "models": config.models,
            "embedding_dimension": config.embedding_dimension,
            "credential_ciphertext_sha256": _require_sha256(credential_ciphertext_hash),
        }
    )


def rag_fingerprint(engine_config: Mapping[str, object]) -> str:
    """Hash the explicit engine/database/storage configuration without credentials."""

    return _fingerprint({"schema": 1, "engine": engine_config})


def _restrict_path(path: Path) -> None:
    if os.name != "nt":
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        return

    from ctypes import wintypes

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    advapi32.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    advapi32.SetFileSecurityW.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    current_sid = _current_user_sid()
    descriptor = ctypes.c_void_p()
    descriptor_size = wintypes.DWORD()
    sddl = f"D:P(A;;FA;;;{current_sid})(A;;FA;;;SY)"
    if not advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), ctypes.byref(descriptor_size)
    ):
        raise OSError("validation path ACL could not be constructed")
    try:
        if not advapi32.SetFileSecurityW(str(path), 0x00000004, descriptor):
            raise OSError("validation path ACL could not be applied")
    finally:
        kernel32.LocalFree(descriptor)


class ValidationStore:
    """Read and atomically replace small, strict validation reports."""

    def __init__(
        self,
        root: Path,
        *,
        secure_path: Callable[[Path], None] = _restrict_path,
    ) -> None:
        self._root = Path(root)
        self._secure_path = secure_path
        self._root.mkdir(parents=True, exist_ok=True)
        self._secure_path(self._root)

    @staticmethod
    def _name(kind: ValidationKind) -> str:
        try:
            return _REPORT_NAMES[kind]
        except KeyError:
            raise ValueError("unknown validation kind") from None

    def read(
        self, kind: ValidationKind, expected_fingerprint: str
    ) -> ValidationReport | None:
        _require_sha256(expected_fingerprint)
        path = self._root / self._name(kind)
        return self._read_path(path, kind, expected_fingerprint)

    @staticmethod
    def _read_path(
        path: Path, kind: ValidationKind, expected_fingerprint: str
    ) -> ValidationReport | None:
        try:
            if path.is_symlink() or path.stat().st_size > _MAX_REPORT_BYTES:
                return None
            payload = path.read_bytes()
        except (FileNotFoundError, OSError):
            return None
        try:
            report = ValidationReport.model_validate_json(payload)
        except ValidationError:
            return None
        if report.kind != kind or report.fingerprint != expected_fingerprint:
            return None
        return report

    def write(self, report: ValidationReport) -> Path:
        target = self._root / self._name(report.kind)
        temporary = self._root / f".{report.kind}.{os.urandom(16).hex()}.tmp"
        payload = json.dumps(
            report.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        if len(payload) > _MAX_REPORT_BYTES:
            raise ValueError("validation report is too large")

        descriptor: int | None = None
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                descriptor = None
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            self._secure_path(temporary)
            os.replace(temporary, target)
            self._secure_path(target)
            return target
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def invalidate(self, kind: ValidationKind) -> Path | None:
        """Archive the previous result before another verification can send requests."""

        target = self._root / self._name(kind)
        if target.is_symlink():
            raise ValueError("validation report path must not be a symlink")
        if not target.exists():
            return None
        archive = self._root / f"{kind}.previous-{os.urandom(16).hex()}.json"
        os.replace(target, archive)
        self._secure_path(archive)
        return archive
