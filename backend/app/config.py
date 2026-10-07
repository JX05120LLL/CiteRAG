"""Explicit environment configuration and narrowly scoped local records."""

import os
import re
import stat
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_RUNTIME_ROOT = (PROJECT_ROOT / ".local" / "runtime").resolve()
QWEATHER_LOCAL_CONFIG = PROJECT_ROOT / "backend" / ".env.weather"
QWEATHER_LOCAL_NAMES = frozenset({
    "CITERAG_QWEATHER_API_HOST", "CITERAG_QWEATHER_API_KEY",
})


def load_qweather_inputs() -> tuple[str | None, str | None, bool]:
    """Resolve a complete environment pair first, then an explicit ignored file.

    The third value is true only for a complete local file. No general dotenv
    loading, variable expansion, or credential logging is permitted.
    """
    host_name, key_name = "CITERAG_QWEATHER_API_HOST", "CITERAG_QWEATHER_API_KEY"
    if host_name in os.environ or key_name in os.environ:
        return os.environ.get(host_name) or None, os.environ.get(key_name) or None, False

    path = QWEATHER_LOCAL_CONFIG
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, None, False
    except OSError:
        raise ValueError("QWeather local config could not be inspected") from None
    if (not stat.S_ISREG(before.st_mode) or before.st_size > 4096
        or getattr(before, "st_file_attributes", 0) & 0x400):
        raise ValueError("QWeather local config must be a regular small file")
    try:
        with path.open("rb") as handle:
            if not os.path.samestat(before, os.fstat(handle.fileno())):
                raise ValueError("QWeather local config changed while loading")
            data = handle.read(4097)
        if len(data) > 4096:
            raise ValueError("QWeather local config is too large")
        lines = data.decode("utf-8-sig").splitlines()
    except (OSError, UnicodeError):
        raise ValueError("QWeather local config could not be read as UTF-8") from None

    values: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        if (not separator or name not in QWEATHER_LOCAL_NAMES or name in values
            or value != value.strip() or value.startswith(("'", '"'))):
            raise ValueError("QWeather local config has an unsupported or duplicate entry")
        values[name] = value
    if set(values) != QWEATHER_LOCAL_NAMES or bool(values[host_name]) != bool(values[key_name]):
        raise ValueError(
            "QWeather local config needs both CITERAG_QWEATHER_API_HOST and "
            "CITERAG_QWEATHER_API_KEY"
        )
    host, key = values[host_name] or None, values[key_name] or None
    return host, key, bool(host and key)


def is_loopback_ip(value: str) -> bool:
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def is_loopback_host(value: str) -> bool:
    return value == "localhost" or is_loopback_ip(value)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True, validate_default=True)

    database_url: SecretStr | None = None
    allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    api_workers: int = Field(default=1, ge=1, le=1)
    ingestion_enabled: bool = False
    answer_enabled: bool = False
    agent_enabled: bool = False
    mcp_enabled: bool = False
    qweather_enabled: bool = False
    qweather_api_host: str | None = Field(default=None, repr=False)
    qweather_api_key: SecretStr | None = None
    backup_enabled: bool = False
    voice_transport_enabled: bool = False
    voice_assistant_enabled: bool = False
    voice_asr_key: SecretStr | None = None
    voice_asr_app_key: SecretStr | None = None
    voice_asr_resource: str = "volc.seedasr.sauc.duration"
    voice_tts_key: SecretStr | None = None
    voice_tts_voice: str = "male-qn-qingse"
    voice_vad_model: Path | None = None
    livekit_url: str = "ws://127.0.0.1:7880"
    livekit_api_key: SecretStr | None = None
    livekit_api_secret: SecretStr | None = None
    backup_pg_bin: Path | None = None
    backup_root: Path = PROJECT_ROOT / ".local" / "backups"
    rag_database_record: Path = LOCAL_RUNTIME_ROOT / "rag-postgres" / "credential.xml"
    rag_workspace_root: Path = LOCAL_RUNTIME_ROOT / "rag-workspaces"

    @field_validator("qweather_api_host")
    @classmethod
    def weather_host(cls, value: str | None) -> str | None:
        if value is not None and (len(value) > 253 or not re.fullmatch(
            r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,3}qweatherapi\.com", value,
        )):
            raise ValueError("QWeather requires its dedicated hostname without a URL or port")
        return value

    @field_validator("qweather_api_key")
    @classmethod
    def weather_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None:
            secret = value.get_secret_value()
            if not 1 <= len(secret) <= 512 or any(not 33 <= ord(char) <= 126 for char in secret):
                raise ValueError("Invalid QWeather credential format")
        return value

    @field_validator("livekit_url")
    @classmethod
    def local_livekit_only(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"ws", "wss"} or not parsed.hostname
            or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
            or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment or "\\" in value
            or any(character.isspace() for character in value)
            or value != f"{parsed.scheme}://{parsed.netloc}"
            or (parsed.port is not None and parsed.port < 1)):
            raise ValueError("LiveKit must be an explicit loopback WebSocket origin")
        return value

    @field_validator("voice_vad_model")
    @classmethod
    def local_vad_model(cls, value: Path | None) -> Path | None:
        if value is not None and not value.resolve().is_relative_to(PROJECT_ROOT / ".local"):
            raise ValueError("VAD model must be prepared in the private project local directory")
        return value.resolve() if value is not None else None

    @field_validator("voice_asr_resource")
    @classmethod
    def speech_resource(cls, value: str) -> str:
        if value not in {"volc.seedasr.sauc.duration", "volc.seedasr.sauc.concurrent",
                         "volc.bigasr.sauc.duration", "volc.bigasr.sauc.concurrent"}:
            raise ValueError("Unsupported ASR resource")
        return value

    @field_validator("voice_tts_voice")
    @classmethod
    def speech_voice(cls, value: str) -> str:
        if not value or len(value) > 100 or any(ord(char) < 32 for char in value):
            raise ValueError("Invalid TTS voice identifier")
        return value

    @model_validator(mode="after")
    def valid_backup(self):
        if self.backup_root.resolve() != (PROJECT_ROOT / ".local" / "backups").resolve():
            raise ValueError("Backup destination is fixed inside the local private directory")
        if self.backup_enabled:
            suffix = ".exe" if os.name == "nt" else ""
            if (self.backup_pg_bin is None or not all(
                (self.backup_pg_bin / (name + suffix)).is_file()
                for name in ("pg_dump", "pg_restore")
            )):
                raise ValueError("Backup requires PostgreSQL client tools")
        return self

    @field_validator("database_url")
    @classmethod
    def postgres_only(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return value
        try:
            url = make_url(value.get_secret_value())
            if url.drivername != "postgresql+asyncpg" or not url.database or not url.host:
                raise ValueError("A PostgreSQL asyncpg URL with host and database is required")
        except ArgumentError:
            raise ValueError("Invalid PostgreSQL database URL") from None
        return value

    @field_validator("allowed_origins")
    @classmethod
    def validate_origins(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if not values:
            raise ValueError("At least one explicit browser origin is required")
        for origin in values:
            parsed = urlsplit(origin)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path
                or parsed.query
                or parsed.fragment
                or "*" in origin
                or "\\" in origin
                or any(character.isspace() for character in origin)
                or origin != f"{parsed.scheme}://{parsed.netloc}"
                or (parsed.port is not None and parsed.port < 1)
            ):
                raise ValueError("Origins must be explicit HTTP(S) origins without paths")
            if not is_loopback_host(parsed.hostname):
                raise ValueError("Only loopback browser origins are supported")
        return values

    @field_validator("rag_database_record", "rag_workspace_root")
    @classmethod
    def validate_local_runtime_path(cls, value: Path, info: ValidationInfo) -> Path:
        resolved = value.resolve()
        if not resolved.is_relative_to(LOCAL_RUNTIME_ROOT):
            raise ValueError("RAG paths must stay under the project local runtime directory")
        expected = {
            "rag_database_record": LOCAL_RUNTIME_ROOT / "rag-postgres" / "credential.xml",
            "rag_workspace_root": LOCAL_RUNTIME_ROOT / "rag-workspaces",
        }[info.field_name]
        if resolved != expected:
            raise ValueError("RAG runtime paths are fixed for the local installation")
        return resolved

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict = {}
        names = {
            "CITERAG_DATABASE_URL": "database_url",
            "CITERAG_API_WORKERS": "api_workers",
            "CITERAG_INGESTION_ENABLED": "ingestion_enabled",
            "CITERAG_ANSWER_ENABLED": "answer_enabled",
            "CITERAG_AGENT_ENABLED": "agent_enabled",
            "CITERAG_MCP_ENABLED": "mcp_enabled",
            "CITERAG_QWEATHER_ENABLED": "qweather_enabled",
            "CITERAG_BACKUP_ENABLED": "backup_enabled",
            "CITERAG_VOICE_TRANSPORT_ENABLED": "voice_transport_enabled",
            "CITERAG_VOICE_ASSISTANT_ENABLED": "voice_assistant_enabled",
            "CITERAG_VOICE_ASR_KEY": "voice_asr_key",
            "CITERAG_VOICE_ASR_APP_KEY": "voice_asr_app_key",
            "CITERAG_VOICE_ASR_RESOURCE": "voice_asr_resource",
            "CITERAG_VOICE_TTS_KEY": "voice_tts_key",
            "CITERAG_VOICE_TTS_VOICE": "voice_tts_voice",
            "CITERAG_VOICE_VAD_MODEL": "voice_vad_model",
            "CITERAG_LIVEKIT_URL": "livekit_url",
            "CITERAG_LIVEKIT_API_KEY": "livekit_api_key",
            "CITERAG_LIVEKIT_API_SECRET": "livekit_api_secret",
            "CITERAG_BACKUP_PG_BIN": "backup_pg_bin",
            "CITERAG_RAG_DATABASE_RECORD": "rag_database_record",
            "CITERAG_RAG_WORKSPACE_ROOT": "rag_workspace_root",
        }
        for env_name, field_name in names.items():
            if os.environ.get(env_name):
                values[field_name] = os.environ[env_name]
        weather_host, weather_key, file_enabled = load_qweather_inputs()
        if weather_host:
            values["qweather_api_host"] = weather_host
        if weather_key:
            values["qweather_api_key"] = weather_key
        if file_enabled and "CITERAG_QWEATHER_ENABLED" not in os.environ:
            values["qweather_enabled"] = True
        if os.environ.get("CITERAG_ALLOWED_ORIGINS"):
            values["allowed_origins"] = tuple(
                value.strip() for value in os.environ["CITERAG_ALLOWED_ORIGINS"].split(",")
            )
        if os.environ.get("WEB_CONCURRENCY") not in {None, "", "1"}:
            raise ValueError("CiteRAG requires exactly one API worker")
        settings = cls(**values)
        if not settings.voice_assistant_enabled:
            return settings
        # Explicitly enabled voice may use the workstation's existing DPAPI
        # records. Environment keys take precedence; no file is written and
        # no supplier request is made by configuration loading.
        from app.credentials import CredentialError, load_credential

        saved: dict = {}
        for provider, field in (("volcengine", "voice_asr_key"), ("minimax", "voice_tts_key")):
            if getattr(settings, field) is not None:
                continue
            # Do not combine an explicit legacy App ID with a saved API Key.
            if provider == "volcengine" and settings.voice_asr_app_key is not None:
                continue
            try:
                record = load_credential(
                    PROJECT_ROOT / ".local/runtime/models" / f"{provider}.credential.xml",
                    provider,
                )
            except CredentialError:
                # The public capability remains not_configured. File/provider
                # diagnostics and credential contents never cross this boundary.
                continue
            saved[field] = record.secret
            if provider == "volcengine" and record.auth_mode == "app-token":
                saved["voice_asr_app_key"] = SecretStr(record.username)
        return settings.model_copy(update=saved)
