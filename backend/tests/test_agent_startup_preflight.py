"""Read-only Agent startup checks with synthetic packages and database metadata."""

import builtins
import hashlib
import json
import re
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import doctor

URL = "postgresql+asyncpg://synthetic@127.0.0.1:1/synthetic"
AGENT_PACKAGES = {"langgraph", "langgraph-checkpoint-postgres", "psycopg", "psycopg-pool"}
TABLES = {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
RUN_PERMISSIONS = {
    "checkpoints": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "checkpoint_blobs": {"SELECT", "INSERT", "DELETE"},
    "checkpoint_writes": {"SELECT", "INSERT", "UPDATE", "DELETE"},
    "checkpoint_migrations": {"SELECT"},
}


def fake_optional_packages(monkeypatch, *, missing=None):
    original = doctor.importlib.metadata.version

    def version(name):
        if name == missing:
            raise doctor.importlib.metadata.PackageNotFoundError(name)
        return "synthetic-version" if name in AGENT_PACKAGES else original(name)

    monkeypatch.setattr(doctor.importlib.metadata, "version", version)
    names = ("langgraph", "langgraph.checkpoint", "langgraph.checkpoint.postgres",
             "langgraph.checkpoint.postgres.aio")
    for name in names:
        module = ModuleType(name)
        module.__path__ = []
        monkeypatch.setitem(sys.modules, name, module)
    sys.modules[names[-1]].AsyncPostgresSaver = SimpleNamespace(MIGRATIONS=["v0", "v1", "v2"])


def fake_database(monkeypatch, *, schema="public", namespace=True, missing_table=None,
                  version=2, error=False, schema_usage=True, denied_permission=None):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    connection = engine.connect.return_value.__aenter__.return_value
    connection.scalars = AsyncMock(return_value=[doctor.EXPECTED_SCHEMA_REVISION])

    async def scalar(statement, parameters=None):
        sql = str(statement)
        if "current_schema()" in sql:
            if error:
                raise RuntimeError("PRIVATE_SYNTHETIC_DRIVER_DIAGNOSTIC")
            return schema
        if "to_regnamespace" in sql:
            return parameters["name"] if namespace else None
        if "to_regclass" in sql:
            return None if parameters["name"].endswith("." + str(missing_table)) else (
                parameters["name"]
            )
        if "has_schema_privilege" in sql:
            assert "'USAGE'" in sql
            return schema_usage
        if "has_table_privilege" in sql:
            privileges = set(re.findall(r"has_table_privilege\(:name, '([A-Z]+)'\)", sql))
            assert privileges, sql
            if denied_permission is None:
                return True
            table, permission = denied_permission
            return not parameters["name"].endswith("." + table) or permission not in privileges
        if "max(v)" in sql:
            return version
        if "count(*) FROM local_profiles" in sql:
            return 1
        if "ingestion_jobs" in sql:
            return False
        assert sql == "SELECT 1", sql
        return 1

    connection.scalar = AsyncMock(side_effect=scalar)
    monkeypatch.setattr(doctor, "create_async_engine", MagicMock(return_value=engine))
    return engine, connection


async def test_default_checks_do_not_import_optional_packages_or_read_checkpoints(monkeypatch):
    engine, connection = fake_database(monkeypatch)
    original_import = builtins.__import__

    def checked_import(name, *args, **kwargs):
        assert not name.startswith(("langgraph", "psycopg", "app.agent")), name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", checked_import)
    checks = await doctor.database_checks(URL)
    assert checks[-1].code == "business_database" and checks[-1].state == "ready"
    sql = " ".join(str(call.args[0]) for call in connection.scalar.await_args_list)
    assert "checkpoint" not in sql and "current_schema" not in sql and "privilege" not in sql
    engine.dispose.assert_awaited_once()


def test_default_dependency_metadata_excludes_agent_packages(monkeypatch):
    observed = []

    def version(name):
        assert name not in AGENT_PACKAGES, name
        observed.append(name)
        return "synthetic-base-package"

    monkeypatch.setattr(doctor.importlib.metadata, "version", version)
    checks = doctor.dependency_checks("text")
    assert set(observed) == {"fastapi", "uvicorn", "sqlalchemy", "asyncpg"}
    assert not any(item.code.startswith("agent_") for item in checks)


async def test_unloadable_optional_package_blocks_without_connecting_or_leaking(monkeypatch):
    fake_optional_packages(monkeypatch)
    engine, _ = fake_database(monkeypatch)
    module = sys.modules["langgraph.checkpoint.postgres.aio"]
    del module.AsyncPostgresSaver

    def inaccessible(_name):
        raise RuntimeError("PRIVATE_SYNTHETIC_DRIVER_DIAGNOSTIC")

    module.__getattr__ = inaccessible
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_dependencies_unavailable"
    assert checks[-1].state == "blocked"
    assert "PRIVATE_SYNTHETIC_DRIVER_DIAGNOSTIC" not in checks[-1].detail
    doctor.create_async_engine.assert_not_called()
    engine.dispose.assert_not_awaited()


async def test_business_schema_gate_still_blocks_before_checkpoint_queries(monkeypatch):
    fake_optional_packages(monkeypatch)
    engine, connection = fake_database(monkeypatch)
    connection.scalars.return_value = ["0002_local_single_user"]
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "database_schema_mismatch" and checks[-1].state == "blocked"
    assert [str(call.args[0]) for call in connection.scalar.await_args_list] == ["SELECT 1"]
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("missing", sorted(AGENT_PACKAGES))
async def test_agent_missing_extras_blocks_before_database_connection(monkeypatch, missing):
    fake_optional_packages(monkeypatch, missing=missing)
    engine, _ = fake_database(monkeypatch)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_dependencies_missing"
    assert checks[-1].state == "blocked" and "--extra agent" in checks[-1].detail
    doctor.create_async_engine.assert_not_called()
    engine.dispose.assert_not_awaited()


@pytest.mark.parametrize("schema", ["public", "synthetic_business"])
async def test_agent_namespace_tables_and_locked_migrations_are_checked_readonly(
    monkeypatch, schema,
):
    fake_optional_packages(monkeypatch)
    engine, connection = fake_database(monkeypatch, schema=schema)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoints" and checks[-1].state == "ready"
    expected = "agent_checkpoints" if schema == "public" else (
        "agent_cp_" + hashlib.md5(schema.encode(), usedforsecurity=False).hexdigest()
    )
    parameters = [call.args[1] for call in connection.scalar.await_args_list if len(call.args) > 1]
    assert {value["name"] for value in parameters} == {
        expected, *(f"{expected}.{table}" for table in TABLES),
    }
    sql = " ".join(str(call.args[0]) for call in connection.scalar.await_args_list)
    assert f'"{expected}".checkpoint_migrations' in sql and "max(v)" in sql
    assert all(str(call.args[0]).lstrip().startswith("SELECT ")
               for call in connection.scalar.await_args_list)
    options = doctor.create_async_engine.call_args.kwargs
    assert options["connect_args"]["server_settings"]["default_transaction_read_only"] == "on"
    assert options["hide_parameters"] is True and options["echo"] is False
    engine.dispose.assert_awaited_once()


async def test_agent_requires_schema_usage_without_creating_or_granting(monkeypatch):
    fake_optional_packages(monkeypatch)
    engine, connection = fake_database(monkeypatch, schema_usage=False)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoint_permissions_missing"
    assert checks[-1].state == "blocked" and "USAGE" in checks[-1].detail
    sql = " ".join(str(call.args[0]) for call in connection.scalar.await_args_list)
    assert "has_schema_privilege" in sql and "max(v)" not in sql
    assert "GRANT " not in sql.upper() and "CREATE " not in sql.upper()
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("table,permission", [
    (table, permission)
    for table, permissions in RUN_PERMISSIONS.items()
    for permission in sorted(permissions)
])
async def test_agent_rejects_each_missing_runtime_or_cleanup_permission(
    monkeypatch, table, permission,
):
    fake_optional_packages(monkeypatch)
    engine, connection = fake_database(monkeypatch, denied_permission=(table, permission))
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoint_permissions_missing"
    assert checks[-1].state == "blocked" and table in checks[-1].detail
    assert "权限" in checks[-1].detail and "不自动" in checks[-1].detail
    assert not any("max(v)" in str(call.args[0])
                   for call in connection.scalar.await_args_list)
    assert all(str(call.args[0]).lstrip().startswith("SELECT ")
               for call in connection.scalar.await_args_list)
    engine.dispose.assert_awaited_once()


async def test_agent_permissions_match_runtime_and_retention_without_requiring_ddl(monkeypatch):
    fake_optional_packages(monkeypatch)
    _, connection = fake_database(monkeypatch)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoints" and checks[-1].state == "ready"
    observed = {}
    for call in connection.scalar.await_args_list:
        sql = str(call.args[0])
        if "has_table_privilege" in sql:
            table = call.args[1]["name"].rsplit(".", 1)[-1]
            observed[table] = set(re.findall(r"has_table_privilege\(:name, '([A-Z]+)'\)", sql))
            assert sql.count(" AND ") == len(RUN_PERMISSIONS[table]) - 1
            assert " OR " not in sql
    assert observed == RUN_PERMISSIONS
    sql = " ".join(str(call.args[0]) for call in connection.scalar.await_args_list)
    assert "has_schema_privilege(:name, 'USAGE')" in sql
    assert "CREATE" not in sql and "GRANT" not in sql


@pytest.mark.parametrize("missing_table", sorted(TABLES))
async def test_missing_checkpoint_table_does_not_setup_or_migrate(monkeypatch, missing_table):
    fake_optional_packages(monkeypatch)
    engine, _ = fake_database(monkeypatch, missing_table=missing_table)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoint_tables_missing"
    assert checks[-1].state == "blocked" and "app.agent.checkpoints" in checks[-1].detail
    engine.dispose.assert_awaited_once()


async def test_missing_namespace_is_actionable_without_database_writes(monkeypatch):
    fake_optional_packages(monkeypatch)
    engine, _ = fake_database(monkeypatch, namespace=False)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoint_namespace_missing"
    assert checks[-1].state == "blocked" and "备份" in checks[-1].detail
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize("code", [
    "agent_dependencies_missing", "agent_checkpoint_namespace_missing",
    "agent_checkpoint_tables_missing", "agent_checkpoint_version_mismatch",
    "agent_checkpoint_permissions_missing",
])
def test_agent_blocked_report_points_to_explicit_preparation_without_migration(code):
    output = doctor.Report("text", [doctor.Check(code, "blocked", "synthetic")]).render()
    assert "Get-Content -LiteralPath .\\backend\\AGENT.md" in output
    assert "uv sync --locked --extra agent" in output
    assert "授权" in output and "备份" in output
    assert "alembic upgrade" not in output and "python -m app.agent.checkpoints" not in output


@pytest.mark.parametrize("version", [None, 1, 3])
async def test_checkpoint_version_must_match_installed_saver_migrations(monkeypatch, version):
    fake_optional_packages(monkeypatch)
    engine, _ = fake_database(monkeypatch, version=version)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoint_version_mismatch"
    assert checks[-1].state == "blocked" and "备份" in checks[-1].detail
    engine.dispose.assert_awaited_once()


async def test_checkpoint_driver_exception_is_sanitized_and_engine_closed(monkeypatch):
    fake_optional_packages(monkeypatch)
    engine, _ = fake_database(monkeypatch, error=True)
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "agent_checkpoint_read_failed"
    assert checks[-1].state == "blocked"
    assert "PRIVATE_SYNTHETIC_DRIVER_DIAGNOSTIC" not in json.dumps(
        [item.__dict__ for item in checks]
    )
    engine.dispose.assert_awaited_once()


async def test_agent_pool_close_failure_is_sanitized(monkeypatch):
    fake_optional_packages(monkeypatch)
    engine, _ = fake_database(monkeypatch)
    engine.dispose.side_effect = RuntimeError("PRIVATE_SYNTHETIC_DRIVER_DIAGNOSTIC")
    checks = await doctor.database_checks(URL, agent=True)
    assert checks[-1].code == "database_close_failed" and checks[-1].state == "blocked"
    assert "PRIVATE_SYNTHETIC_DRIVER_DIAGNOSTIC" not in json.dumps(
        [item.__dict__ for item in checks]
    )


def test_agent_cli_forwards_opt_in_to_both_preflight_stages(monkeypatch, capsys):
    monkeypatch.setenv("CITERAG_DATABASE_URL", URL)
    monkeypatch.setattr(sys, "argv", ["doctor", "--profile", "text", "--database", "--agent",
                                      "--backend-only"])
    inspection = MagicMock(return_value=doctor.Report("text", []))
    database = AsyncMock(return_value=[doctor.Check("agent_checkpoints", "ready", "synthetic")])
    monkeypatch.setattr(doctor, "inspect", inspection)
    monkeypatch.setattr(doctor, "database_checks", database)
    assert doctor.main() == 0
    assert inspection.call_args.kwargs["agent"] is True
    database.assert_awaited_once_with(URL, agent=True)
    assert "synthetic" in capsys.readouterr().out
