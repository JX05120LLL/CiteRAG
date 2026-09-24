"""Derive public capability states from local verification evidence."""

from __future__ import annotations

import stat
from pathlib import Path
from typing import Any, Literal

from app.config import PROJECT_ROOT
from app.credentials import CredentialError, DashScopeConfig, load_dashscope_config
from app.rag.database import (
    RagDatabaseError,
    RagDatabaseProbe,
    RagDatabaseSettings,
    check_rag_database_alive,
    load_rag_database_settings,
)
from app.rag.runtime import MODEL_CREDENTIAL_RECORD
from app.rag.sdk import LIGHTRAG_COMMIT
from app.rag.verify import rag_configuration_fingerprint
from app.validation import ValidationReport, ValidationStore, model_fingerprint

VALIDATION_ROOT = PROJECT_ROOT / ".local/runtime/validation"


def _record_state(path: Path) -> Literal["missing", "present", "unavailable"]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "unavailable"
    return "present" if stat.S_ISREG(metadata.st_mode) else "unavailable"


def build_capability_status(
    model_config: DashScopeConfig | None,
    rag_database: RagDatabaseSettings | None,
    model_report: ValidationReport | None,
    rag_report: ValidationReport | None,
    rag_probe: RagDatabaseProbe | None,
    *,
    model_configuration_error: bool = False,
    rag_configuration_error: bool = False,
) -> dict[str, object]:
    """Return only allow-listed public metadata; never serialize config objects."""

    result: dict[str, object] = {}
    if model_configuration_error:
        result["models"] = "unavailable"
    elif model_config is None:
        result["models"] = "not_configured"
    else:
        current_model_report = (
            model_report
            if model_report is not None
            and model_report.kind == "models"
            and model_report.fingerprint
            == model_fingerprint(
                model_config, model_config.credential_ciphertext_sha256
            )
            else None
        )
        result["models"] = (
            current_model_report.outcome
            if current_model_report is not None
            else "unverified"
        )
        model_info: dict[str, object] = {
            "region": model_config.region,
            "model_names": [
                model_config.models[role]
                for role in ("answer", "engine", "summary", "embedding", "rerank")
            ],
        }
        if current_model_report is not None:
            model_info["last_verified_at"] = current_model_report.verified_at.isoformat()
        result["models_info"] = model_info

    if rag_configuration_error:
        result["rag"] = "unavailable"
    elif rag_database is None or model_config is None:
        result["rag"] = "not_configured"
    else:
        current_rag_report = (
            rag_report
            if rag_report is not None
            and rag_report.kind == "rag"
            and rag_report.fingerprint
            == rag_configuration_fingerprint(model_config, rag_database)
            else None
        )
        if rag_probe is None or not rag_probe.read_write_ok:
            result["rag"] = "unavailable"
        else:
            result["rag"] = (
                current_rag_report.outcome
                if current_rag_report is not None
                else "unverified"
            )
        rag_info: dict[str, object] = {"lightrag_commit": LIGHTRAG_COMMIT}
        if rag_probe is not None:
            rag_info["postgresql_major"] = rag_probe.postgresql_major
            rag_info["vector_version"] = rag_probe.vector_version
        if current_rag_report is not None:
            rag_info["last_verified_at"] = current_rag_report.verified_at.isoformat()
        result["rag_info"] = rag_info
    return result


async def load_capability_status(state: Any) -> dict[str, object]:
    """Read local evidence without network/provider calls or serialized secrets."""

    model_config: DashScopeConfig | None = None
    rag_database: RagDatabaseSettings | None = None
    model_error = False
    rag_error = False
    model_path_state = _record_state(Path(MODEL_CREDENTIAL_RECORD))
    if model_path_state == "unavailable":
        model_error = True
    elif model_path_state == "present":
        try:
            model_config = load_dashscope_config(MODEL_CREDENTIAL_RECORD)
        except (CredentialError, OSError, ValueError):
            model_error = True
    rag_path = state.settings.rag_database_record
    rag_path_state = _record_state(rag_path)
    if rag_path_state == "unavailable":
        rag_error = True
    elif rag_path_state == "present":
        try:
            rag_database = load_rag_database_settings(rag_path)
        except (RagDatabaseError, OSError, ValueError):
            rag_error = True

    model_report = None
    rag_report = None
    if model_config is not None or rag_database is not None:
        try:
            store = ValidationStore(VALIDATION_ROOT)
            if model_config is not None:
                model_report = store.read(
                    "models",
                    model_fingerprint(
                        model_config, model_config.credential_ciphertext_sha256
                    ),
                )
            if model_config is not None and rag_database is not None:
                rag_report = store.read(
                    "rag", rag_configuration_fingerprint(model_config, rag_database)
                )
        except (OSError, ValueError):
            model_error |= model_config is not None
            rag_error |= rag_database is not None

    runtime = state.rag_runtime
    probe = state.rag_probe
    if runtime is None or runtime.database_settings != rag_database:
        probe = None
    if probe is not None and rag_database is not None:
        if not await check_rag_database_alive(
            rag_database,
            postgresql_major=probe.postgresql_major,
            vector_version=probe.vector_version,
        ):
            probe = None
    return build_capability_status(
        model_config,
        rag_database,
        model_report,
        rag_report,
        probe,
        model_configuration_error=model_error,
        rag_configuration_error=rag_error,
    )
