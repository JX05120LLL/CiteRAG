"""Strict local engine-database configuration and bounded pgvector probing."""

from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
from pydantic import SecretStr

from app.credentials import (
    MAX_CREDENTIAL_BYTES,
    CredentialError,
    PathSecurity,
    _current_user_sid,
    _local_name,
    _parse_credential,
    _parse_document,
    _probe_path_security,
    _required_text,
    decrypt_dpapi,
    validate_path_security,
)

ENGINE_IMAGE = "docker.io/pgvector/pgvector:0.8.1-pg17"
APPROVED_IMAGE_DIGEST = (
    "sha256:3e8b3adfd27b5707128f60956f62a793c3c9326ea8cfaf0eab7adccb5d700b21"
)
_RECORD_FIELDS = {
    "SchemaVersion",
    "Provider",
    "AuthMode",
    "Host",
    "Port",
    "Database",
    "Username",
    "Container",
    "Image",
    "ImageDigest",
    "Credential",
    "Verified",
}


class RagDatabaseError(RuntimeError):
    """A category-only engine database failure that never contains connection material."""


@dataclass(frozen=True)
class RagDatabaseSettings:
    host: str
    port: int
    database: str
    username: str
    password: SecretStr
    container: str
    image: str
    image_digest: str
    credential_ciphertext_sha256: str


@dataclass(frozen=True)
class RagDatabaseProbe:
    postgresql_major: int
    vector_version: str
    read_write_ok: bool


async def check_rag_database_alive(
    settings: RagDatabaseSettings,
    *,
    postgresql_major: int,
    vector_version: str,
    connect: Callable[..., Awaitable[Any]] = asyncpg.connect,
) -> bool:
    """Recheck liveness for status without writing to the engine database."""

    connection: Any | None = None
    healthy = False
    try:
        connection = await connect(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            user=settings.username,
            password=settings.password.get_secret_value(),
            timeout=2,
            command_timeout=2,
            server_settings={
                "application_name": "citerag-rag-status",
                "default_transaction_read_only": "on",
            },
        )
        raw_version = await connection.fetchval("SHOW server_version_num")
        observed_vector = await connection.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = $1", "vector"
        )
        healthy = (
            int(str(raw_version)) // 10_000 == postgresql_major
            and observed_vector == vector_version
        )
    except (OSError, TimeoutError, TypeError, ValueError, asyncpg.PostgresError):
        healthy = False
    finally:
        if connection is not None:
            try:
                await connection.close(timeout=3)
            except (OSError, TimeoutError, asyncpg.PostgresError):
                healthy = False
    return healthy


def _validate_rag_path(path: Path) -> None:
    absolute = Path(os.path.abspath(path))
    if (
        absolute.name.lower() != "credential.xml"
        or tuple(part.lower() for part in absolute.parent.parts[-3:])
        != (".local", "runtime", "rag-postgres")
    ):
        raise RagDatabaseError("engine database credential path is outside local runtime")
    try:
        current_sid = _current_user_sid()
        validate_path_security(
            absolute,
            _probe_path_security(absolute),
            current_user_sid=current_sid,
        )
        directory_security = _probe_path_security(absolute.parent)
        validate_path_security(
            absolute.parent,
            PathSecurity(
                is_file=absolute.parent.is_dir(),
                has_reparse_point=directory_security.has_reparse_point,
                owner_sid=directory_security.owner_sid,
                dacl_protected=directory_security.dacl_protected,
                allowed_sids=directory_security.allowed_sids,
            ),
            current_user_sid=current_sid,
        )
    except CredentialError:
        raise RagDatabaseError("engine database credential path is not private") from None


def _read_private_record(path: Path) -> bytes:
    _validate_rag_path(path)
    try:
        initial = os.stat(path, follow_symlinks=False)
        if initial.st_size < 1 or initial.st_size > MAX_CREDENTIAL_BYTES:
            raise RagDatabaseError("engine database credential structure is invalid")
        with path.open("rb") as handle:
            if not os.path.samestat(initial, os.fstat(handle.fileno())):
                raise RagDatabaseError("engine database credential changed while loading")
            data = handle.read(MAX_CREDENTIAL_BYTES + 1)
    except RagDatabaseError:
        raise
    except OSError:
        raise RagDatabaseError("engine database credential could not be read") from None
    if len(data) > MAX_CREDENTIAL_BYTES:
        raise RagDatabaseError("engine database credential structure is invalid")
    return data


def _required_integer(fields: dict[str, ET.Element], name: str) -> int:
    try:
        return int(_required_text(fields, name, "I32"))
    except (CredentialError, ValueError):
        raise RagDatabaseError("engine database credential structure is invalid") from None


def load_rag_database_settings(path: Path) -> RagDatabaseSettings:
    path = Path(path)
    data = _read_private_record(path)
    try:
        schema, fields = _parse_document(data)
        if schema != 1 or set(fields) != _RECORD_FIELDS:
            raise CredentialError
        values = {
            "provider": _required_text(fields, "Provider"),
            "auth_mode": _required_text(fields, "AuthMode"),
            "host": _required_text(fields, "Host"),
            "database": _required_text(fields, "Database"),
            "username": _required_text(fields, "Username"),
            "container": _required_text(fields, "Container"),
            "image": _required_text(fields, "Image"),
            "image_digest": _required_text(fields, "ImageDigest"),
        }
        port = _required_integer(fields, "Port")
        verified = _required_text(fields, "Verified", "B")
        credential_element = fields["Credential"]
        if _local_name(credential_element.tag) != "Obj":
            raise CredentialError
        credential_username, encrypted_hex = _parse_credential(credential_element)
    except (CredentialError, KeyError):
        raise RagDatabaseError("engine database credential structure is invalid") from None

    expected = {
        "provider": "postgresql",
        "auth_mode": "password",
        "host": "127.0.0.1",
        "database": "assistant_rag",
        "username": "citerag_rag",
        "container": "citerag-rag-postgres",
        "image": ENGINE_IMAGE,
        "image_digest": APPROVED_IMAGE_DIGEST,
    }
    if values != expected or port != 55433 or verified != "false":
        raise RagDatabaseError("engine database credential is incompatible")
    if credential_username != values["username"]:
        raise RagDatabaseError("engine database credential is incompatible")
    try:
        if not encrypted_hex or len(encrypted_hex) % 2:
            raise ValueError
        ciphertext = bytes.fromhex(encrypted_hex)
    except ValueError:
        raise RagDatabaseError("engine database encrypted credential is invalid") from None
    try:
        password = decrypt_dpapi(ciphertext)
    except CredentialError:
        raise RagDatabaseError("engine database credential could not be decrypted") from None
    if not password:
        raise RagDatabaseError("engine database credential is empty")
    return RagDatabaseSettings(
        host=values["host"],
        port=port,
        database=values["database"],
        username=values["username"],
        password=SecretStr(password),
        container=values["container"],
        image=values["image"],
        image_digest=values["image_digest"],
        credential_ciphertext_sha256=hashlib.sha256(ciphertext).hexdigest(),
    )


async def probe_rag_database(
    settings: RagDatabaseSettings,
    *,
    connect: Callable[..., Awaitable[Any]] = asyncpg.connect,
) -> RagDatabaseProbe:
    connection: Any | None = None
    schema_name = f"citerag_probe_{uuid4().hex}"
    schema_created = False
    try:
        connection = await connect(
            host=settings.host,
            port=settings.port,
            database=settings.database,
            user=settings.username,
            password=settings.password.get_secret_value(),
            timeout=5,
            command_timeout=5,
            server_settings={"application_name": "citerag-rag-probe"},
        )
        raw_version = await connection.fetchval("SHOW server_version_num")
        try:
            major = int(str(raw_version)) // 10_000
        except (TypeError, ValueError):
            raise RagDatabaseError("postgresql_version") from None
        if major != 17:
            raise RagDatabaseError("postgresql_version")

        vector_version = await connection.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = $1", "vector"
        )
        if vector_version is None:
            raise RagDatabaseError("vector_extension")
        if vector_version != "0.8.1":
            raise RagDatabaseError("vector_version")

        role = await connection.fetchrow(
            "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication "
            "FROM pg_roles WHERE rolname = current_user"
        )
        if role is None or any(
            bool(role[name])
            for name in ("rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication")
        ):
            raise RagDatabaseError("role_privileges")

        quoted_schema = f'"{schema_name}"'
        await connection.execute(f"CREATE SCHEMA {quoted_schema}")
        schema_created = True
        await connection.execute(
            f"CREATE TABLE {quoted_schema}.probe (value text NOT NULL)"
        )
        await connection.execute(
            f"INSERT INTO {quoted_schema}.probe (value) VALUES ($1)",
            "citerag-probe",
        )
        observed = await connection.fetchval(
            f"SELECT value FROM {quoted_schema}.probe LIMIT 1"
        )
        if observed != "citerag-probe":
            raise RagDatabaseError("read_write_probe")
        return RagDatabaseProbe(
            postgresql_major=major,
            vector_version=vector_version,
            read_write_ok=True,
        )
    except RagDatabaseError:
        raise
    except (OSError, TimeoutError, asyncpg.PostgresError):
        raise RagDatabaseError("connection") from None
    finally:
        if connection is not None:
            cleanup_failed = False
            try:
                if schema_created:
                    await connection.execute(f'DROP SCHEMA "{schema_name}" CASCADE')
            except (OSError, TimeoutError, asyncpg.PostgresError):
                cleanup_failed = True
            try:
                await connection.close(timeout=3)
            except (OSError, TimeoutError, asyncpg.PostgresError):
                cleanup_failed = True
            if cleanup_failed:
                raise RagDatabaseError("cleanup") from None
