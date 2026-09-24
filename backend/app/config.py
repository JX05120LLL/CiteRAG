"""Explicit environment configuration. No dotenv files are loaded."""

import os
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
    backup_enabled: bool = False
    backup_pg_bin: Path | None = None
    backup_root: Path = PROJECT_ROOT / ".local" / "backups"
    rag_database_record: Path = LOCAL_RUNTIME_ROOT / "rag-postgres" / "credential.xml"
    rag_workspace_root: Path = LOCAL_RUNTIME_ROOT / "rag-workspaces"

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
            "CITERAG_BACKUP_ENABLED": "backup_enabled",
            "CITERAG_BACKUP_PG_BIN": "backup_pg_bin",
            "CITERAG_RAG_DATABASE_RECORD": "rag_database_record",
            "CITERAG_RAG_WORKSPACE_ROOT": "rag_workspace_root",
        }
        for env_name, field_name in names.items():
            if os.environ.get(env_name):
                values[field_name] = os.environ[env_name]
        if os.environ.get("CITERAG_ALLOWED_ORIGINS"):
            values["allowed_origins"] = tuple(
                value.strip() for value in os.environ["CITERAG_ALLOWED_ORIGINS"].split(",")
            )
        if os.environ.get("WEB_CONCURRENCY") not in {None, "", "1"}:
            raise ValueError("CiteRAG requires exactly one API worker")
        return cls(**values)
