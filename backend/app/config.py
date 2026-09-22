"""Explicit environment configuration. No dotenv files are loaded."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def is_loopback_ip(value: str) -> bool:
    try:
        return ip_address(value).is_loopback
    except ValueError:
        return False


def is_loopback_host(value: str) -> bool:
    return value == "localhost" or is_loopback_ip(value)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    database_url: SecretStr | None = None
    allowed_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    api_workers: int = Field(default=1, ge=1, le=1)

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

    @classmethod
    def from_env(cls) -> "Settings":
        values: dict = {}
        names = {
            "CITERAG_DATABASE_URL": "database_url",
            "CITERAG_API_WORKERS": "api_workers",
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
