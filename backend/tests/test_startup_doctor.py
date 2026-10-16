"""Startup diagnostics must stay read-only and keep configuration private."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app import doctor


def test_ui_profile_does_not_load_credentials_or_connect(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "dependency_checks", lambda _: [])
    monkeypatch.setattr(doctor, "port_check", lambda *_: doctor.Check("api_port", "ready", "free"))
    monkeypatch.setattr(
        doctor, "file_check", lambda *_: pytest.fail("UI read private configuration")
    )
    monkeypatch.setattr(doctor, "database_checks", AsyncMock(side_effect=AssertionError("DB read")))
    report = doctor.inspect("ui", {}, tmp_path, frontend=False)
    assert report.exit_code == 0
    assert "界面" in report.summary


def test_missing_database_has_actionable_non_destructive_advice(monkeypatch, tmp_path):
    monkeypatch.setattr(doctor, "dependency_checks", lambda _: [])
    monkeypatch.setattr(doctor, "port_check", lambda *_: doctor.Check("api_port", "ready", "free"))
    report = doctor.inspect("text", {}, tmp_path)
    assert report.exit_code == 1
    assert any(item.code == "database_not_configured" for item in report.checks)
    assert "授权" in report.render()
    assert "未初始化" in report.render()


@pytest.mark.parametrize("code,expected", [
    ("backend_dependencies", "uv sync --locked"),
    ("database_not_configured", "Get-Content -LiteralPath .\\backend\\STARTUP.md"),
    ("model_record", ".\\backend\\configure-model.ps1 -CheckOnly"),
    ("rag_record", "Get-Content -LiteralPath .\\backend\\README.md"),
    ("database_schema_mismatch", "停写"),
    ("unfinished_ingestion", "审查"),
])
def test_blocked_report_gives_safe_next_steps_without_automatic_repairs(code, expected):
    report = doctor.Report("knowledge", [doctor.Check(code, "blocked", "synthetic missing item")])
    output = report.render()
    assert "下一步" in output and expected in output
    assert "alembic upgrade" not in output and "stamp" not in output
    assert "从仓库根目录" in output


def test_unverified_model_presence_does_not_recommend_overwriting_configuration():
    output = doctor.Report("text", [doctor.Check("model_record", "unverified", "exists")]).render()
    assert "configure-model.ps1" not in output


def test_model_next_step_command_can_be_copied_without_prose_or_punctuation():
    output = doctor.Report("text", [doctor.Check("model_record", "blocked", "missing")]).render()
    command = (
        "  powershell.exe -NoProfile -ExecutionPolicy Bypass "
        "-File .\\backend\\configure-model.ps1 -CheckOnly"
    )
    assert command in output.splitlines()


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://owner:PRIVATE_TEST_PASSWORD@remote.invalid/db",
        "sqlite:///PRIVATE_TEST_PASSWORD",
        "postgresql+asyncpg://owner:PRIVATE_TEST_PASSWORD@127.0.0.1/db?search_path=private",
        "PRIVATE_TEST_PASSWORD is not a connection URL",
    ],
)
def test_rejected_url_never_exposes_credentials(url):
    check = doctor.database_config_check(url)
    assert check.state == "blocked"
    assert "PRIVATE_TEST_PASSWORD" not in json.dumps(check.__dict__)
    assert "remote.invalid" not in check.detail


def test_file_presence_is_unverified_without_reading_content(tmp_path):
    record = tmp_path / "synthetic.xml"
    record.write_text("not a credential", encoding="utf-8")
    check = doctor.file_check("model_record", record)
    assert check.state == "unverified"
    assert "未读取" in check.detail
    assert not Path(str(check.detail)).is_file()


@pytest.mark.parametrize("legacy", ["environment", "file"])
def test_legacy_engine_inputs_block_without_reading_or_removing(monkeypatch, tmp_path, legacy):
    monkeypatch.setattr(doctor, "dependency_checks", lambda _: [])
    monkeypatch.setattr(doctor, "port_check", lambda *_: doctor.Check("port", "ready", "free"))
    environment = (
        {"POSTGRES_WORKSPACE": "PRIVATE_TEST_WORKSPACE"} if legacy == "environment" else {}
    )
    if legacy == "file":
        (tmp_path / "backend").mkdir()
        (tmp_path / "backend/config.ini").write_text("PRIVATE_TEST_WORKSPACE", encoding="utf-8")
    report = doctor.inspect("ui", environment, tmp_path, frontend=False)
    assert report.exit_code == 1
    assert "legacy_engine_configuration" in report.render()
    assert "PRIVATE_TEST_WORKSPACE" not in report.render()
    if legacy == "file":
        assert (tmp_path / "backend/config.ini").exists()


def fake_engine(monkeypatch, *, revisions=None, owner_count=1, jobs=False):
    engine = MagicMock()
    engine.dispose = AsyncMock()
    connection = engine.connect.return_value.__aenter__.return_value
    connection.scalars = AsyncMock(return_value=revisions or ["0012_agent_runs"])
    connection.scalar = AsyncMock(side_effect=[1, owner_count, jobs])
    monkeypatch.setattr(doctor, "create_async_engine", MagicMock(return_value=engine))
    return engine, connection


async def test_database_check_uses_read_only_metadata_not_application_start(monkeypatch):
    engine, connection = fake_engine(monkeypatch)
    checks = await doctor.database_checks("postgresql+asyncpg://synthetic@127.0.0.1:1/synthetic")
    assert checks[-1].state == "ready"
    assert (
        doctor.create_async_engine.call_args.kwargs["connect_args"]["server_settings"][
            "default_transaction_read_only"
        ]
        == "on"
    )
    sql = " ".join(str(call.args[0]) for call in connection.scalar.await_args_list)
    assert "ingestion_jobs" in sql
    assert not any(word in sql.upper() for word in ("INSERT", "DELETE", "CREATE", "UPDATE"))
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "revisions, owners, jobs, expected",
    [
        (["0002_local_single_user"], 1, False, "database_schema_mismatch"),
        (["0012_agent_runs"], 0, False, "database_owner_invalid"),
        (["0012_agent_runs"], 1, True, "unfinished_ingestion"),
    ],
)
async def test_database_gates_do_not_modify_existing_records(
    monkeypatch,
    revisions,
    owners,
    jobs,
    expected,
):
    engine, _ = fake_engine(monkeypatch, revisions=revisions, owner_count=owners, jobs=jobs)
    checks = await doctor.database_checks("postgresql+asyncpg://synthetic@127.0.0.1:1/synthetic")
    assert checks[-1].code == expected
    assert checks[-1].state == "blocked"
    engine.dispose.assert_awaited_once()


async def test_connection_exception_is_sanitized_and_pool_closed(monkeypatch):
    engine, _ = fake_engine(monkeypatch)
    engine.connect.return_value.__aenter__.side_effect = RuntimeError("PRIVATE_TEST_PASSWORD")
    checks = await doctor.database_checks("postgresql+asyncpg://synthetic@127.0.0.1:1/synthetic")
    assert checks[-1].state == "blocked"
    assert "PRIVATE_TEST_PASSWORD" not in json.dumps([item.__dict__ for item in checks])
    engine.dispose.assert_awaited_once()


def test_occupied_port_reports_blocked_without_stopping_owner():
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert doctor.port_check("api_port", listener.getsockname()[1]).state == "blocked"


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
def test_frontend_launcher_drops_backend_environment_before_real_node(tmp_path, shell_name):
    if os.name != "nt":
        pytest.skip("Windows PowerShell startup boundary requires Windows")
    shell, node = shutil.which(shell_name), shutil.which("node.exe")
    if not shell or not node:
        pytest.skip("The selected PowerShell edition and Node.js are required")

    backend = tmp_path / "backend"
    backend.mkdir()
    launcher = backend / "start.ps1"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "start.ps1", launcher)
    probe = tmp_path / "frontend/node_modules/vite/bin/vite.js"
    probe.parent.mkdir(parents=True)
    keys = ("CITERAG_DATABASE_URL", "PGPASSWORD", "DASHSCOPE_API_KEY", "LIVEKIT_API_SECRET")
    probe.write_text(
        "const keys = " + json.dumps(keys) + ";\n"
        "console.log(JSON.stringify({\n"
        "  seenSensitiveKeys: keys.filter(key => Object.hasOwn(process.env, key)),\n"
        "  customVariablePreserved: process.env.P0_FRONTEND_CUSTOM === 'public-synthetic',\n"
        "  frontendArgumentsCorrect: JSON.stringify(process.argv.slice(2)) === "
        "JSON.stringify(['--host', '127.0.0.1', '--port', '5173', '--strictPort'])\n"
        "}));\n",
        encoding="utf-8",
    )
    prefixes = (
        "CITERAG_", "PG", "POSTGRES", "OPENAI_", "DASHSCOPE_", "ANTHROPIC_",
        "VOLCENGINE_", "MINIMAX_", "LIVEKIT_", "PYTHON",
    )
    environment = {
        name: value for name, value in os.environ.items()
        if not name.upper().startswith(prefixes)
        and name.upper() not in {"DATABASE_URL", "WEB_CONCURRENCY"}
    }
    environment.update(dict.fromkeys(keys, "synthetic-only-never-a-real-credential"))
    environment["P0_FRONTEND_CUSTOM"] = "public-synthetic"

    # Prove the same Node probe detects the seeded environment before testing removal.
    control = subprocess.run(
        [node, str(probe)], env=environment, capture_output=True, text=True,
        timeout=15, check=False,
    )
    assert control.returncode == 0, "The synthetic Node environment probe did not run"
    baseline = json.loads(control.stdout)
    assert baseline["seenSensitiveKeys"] == list(keys)
    assert baseline["customVariablePreserved"] is True

    launched = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(launcher), "-Role", "frontend"],
        env=environment, capture_output=True, text=True, timeout=30, check=False,
    )
    assert launched.returncode == 0, "The copied frontend launcher did not complete"
    observed = json.loads(launched.stdout)
    assert observed == {"seenSensitiveKeys": [], "customVariablePreserved": True,
                        "frontendArgumentsCorrect": True}


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize(
    "flags,agent,weather,answer,ingestion,inherited",
    [([], False, False, "false", "false", {}),
     (["-EnableAgent"], True, False, "false", "false", {}),
     (["-EnableWeather"], False, True, "false", "false", {}),
     (["-EnableAgent", "-EnableWeather"], True, True, "false", "false", {}),
     (["-EnableAnswer"], False, False, "true", "false", {}),
     (["-EnableIngestion"], False, False, "false", "true", {}),
     (["-EnableAnswer", "-EnableIngestion", "-EnableAgent", "-EnableWeather"],
      True, True, "true", "true", {}),
     (["-CheckOnly", "-EnableAnswer", "-EnableIngestion", "-EnableAgent", "-EnableWeather"],
      True, True, "true", "true", {}),
     ([], False, False, "true", "true",
      {"CITERAG_ANSWER_ENABLED": "true", "CITERAG_INGESTION_ENABLED": "true"}),
     ([], False, False, "yes", "1",
      {"CITERAG_ANSWER_ENABLED": "yes", "CITERAG_INGESTION_ENABLED": "1"}),
     ([], False, False, "PRIVATE_TEST_BOOL", "false",
      {"CITERAG_ANSWER_ENABLED": "PRIVATE_TEST_BOOL"})],
)
def test_api_launcher_optional_features_are_explicit(
    tmp_path, shell_name, flags, agent, weather, answer, ingestion, inherited,
):
    if os.name != "nt":
        pytest.skip("Windows PowerShell startup boundary requires Windows")
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip("The selected PowerShell edition is required")
    backend = tmp_path / "backend"
    backend.mkdir()
    launcher = backend / "start.ps1"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "start.ps1", launcher)
    # Run the real launcher and Python child, replacing only the doctor/server modules.
    # The temporary application cannot connect a database or start a network server.
    python = backend / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    shutil.copyfile(sys.executable, python)
    shutil.copyfile(Path(sys.prefix) / "pyvenv.cfg", python.parent.parent / "pyvenv.cfg")
    app = backend / "app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    probe = (
        "import json, os, sys\n"
        "print('PUBLIC_PROBE:' + json.dumps({\n"
        "  'args': sys.argv[1:],\n"
        "  'agent': os.environ['CITERAG_AGENT_ENABLED'],\n"
        "  'weather': os.environ['CITERAG_QWEATHER_ENABLED'],\n"
        "  'weather_config_preserved': "
        "os.environ['CITERAG_QWEATHER_API_HOST'] == 'synthetic.qweatherapi.com' "
        "and os.environ['CITERAG_QWEATHER_API_KEY'] == 'public-synthetic',\n"
        "  'mcp': os.environ['CITERAG_MCP_ENABLED'],\n"
        "  'voice': os.environ['CITERAG_VOICE_ASSISTANT_ENABLED'],\n"
        f"  'answers_preserved': os.environ['CITERAG_ANSWER_ENABLED'] == {answer!r},\n"
        f"  'ingestion_preserved': os.environ['CITERAG_INGESTION_ENABLED'] == {ingestion!r}\n"
        "}));\n"
    )
    (app / "doctor.py").write_text(probe, encoding="utf-8")
    (backend / "uvicorn.py").write_text(probe, encoding="utf-8")
    prefixes = ("CITERAG_", "PG", "POSTGRES", "OPENAI_", "DASHSCOPE_", "ANTHROPIC_",
                "VOLCENGINE_", "MINIMAX_", "LIVEKIT_", "PYTHON")
    environment = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith(prefixes)
                   and name.upper() not in {"DATABASE_URL", "WEB_CONCURRENCY"}}
    environment.update({
        "CITERAG_DATABASE_URL": "postgresql+asyncpg://synthetic@127.0.0.1:1/synthetic",
        "CITERAG_AGENT_ENABLED": "true", "CITERAG_MCP_ENABLED": "true",
        "CITERAG_QWEATHER_ENABLED": "true", "CITERAG_VOICE_ASSISTANT_ENABLED": "true",
        "CITERAG_QWEATHER_API_HOST": "synthetic.qweatherapi.com",
        "CITERAG_QWEATHER_API_KEY": "public-synthetic",
        **inherited,
    })
    launched = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(launcher), *flags],
        env=environment, capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False,
    )
    invalid = answer == "PRIVATE_TEST_BOOL"
    assert launched.returncode == (1 if invalid else 0), launched.stderr
    observed = [json.loads(line.removeprefix("PUBLIC_PROBE:"))
                for line in launched.stdout.splitlines() if line.startswith("PUBLIC_PROBE:")]
    assert len(observed) == (0 if invalid else (1 if "-CheckOnly" in flags else 2))
    if not invalid:
        assert observed[0]["args"] == ["--profile", "text", "--database", "--backend-only"] + (
            ["--agent"] if agent else []
        )
    if not invalid and "-CheckOnly" not in flags:
        assert observed[1]["args"] == ["app.main:app", "--host", "127.0.0.1", "--port", "8000",
                                       "--workers", "1", "--no-proxy-headers"]
    for child in observed:
        assert child["agent"] == str(agent).lower()
        assert child["weather"] == str(weather).lower()
        assert child["weather_config_preserved"] is weather
        assert child["mcp"] == child["voice"] == "false"
        assert child["answers_preserved"] and child["ingestion_preserved"]
    answer_state = "true" if answer in {"true", "yes", "1"} else (
        "false" if answer == "false" else "invalid (value hidden)"
    )
    ingestion_state = "true" if ingestion in {"true", "yes", "1"} else "false"
    assert f"Answer={answer_state}; Ingestion={ingestion_state}" in launched.stdout
    assert "PRIVATE_TEST_BOOL" not in launched.stdout + launched.stderr


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
@pytest.mark.parametrize("role", ["api", "frontend"])
def test_guide_works_before_dependencies_without_loading_configuration(tmp_path, shell_name, role):
    if os.name != "nt":
        pytest.skip("Windows PowerShell startup boundary requires Windows")
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip("The selected PowerShell edition is required")
    backend = tmp_path / "backend"
    backend.mkdir()
    launcher = backend / "start.ps1"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "start.ps1", launcher)
    # Both configuration libraries fail if loaded; no Python/Node dependency is installed.
    (backend / "business-config.ps1").write_text("throw 'PRIVATE_TEST_SENTINEL'", encoding="utf-8")
    (backend / "configure-model.ps1").write_text("throw 'PRIVATE_TEST_SENTINEL'", encoding="utf-8")
    private = tmp_path / ".local/runtime/models/dashscope.credential.xml"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"PRIVATE_TEST_SENTINEL")
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for path in tmp_path.rglob("*") if path.is_file()}
    environment = {name: value for name, value in os.environ.items()
                   if not name.upper().startswith(("CITERAG_", "PYTHON"))}
    environment["CITERAG_DATABASE_URL"] = "PRIVATE_TEST_SENTINEL"
    launched = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(launcher), "-Guide", "-Role", role, "-Profile", "knowledge",
         "-EnableAgent", "-EnableWeather", "-EnableAnswer", "-EnableIngestion"],
        env=environment, capture_output=True, text=True, encoding="utf-8",
        timeout=30, check=False,
    )
    assert launched.returncode == 0, launched.stderr
    assert "No configuration was read" in launched.stdout
    assert "-CheckOnly" in launched.stdout and "-Guide" in launched.stdout
    assert "PRIVATE_TEST_SENTINEL" not in launched.stdout + launched.stderr
    if role == "api":
        assert "uv sync --locked --extra rag --extra agent" in launched.stdout
        assert "YOUR_DASHSCOPE_WORKSPACE_ID" in launched.stdout
        assert "-EnableAnswer" in launched.stdout and "-EnableIngestion" in launched.stdout
        assert "backup" in launched.stdout and "authorization" in launched.stdout
    else:
        assert "pnpm install --frozen-lockfile" in launched.stdout
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh.exe"])
def test_missing_environment_failure_has_safe_actionable_guide(tmp_path, shell_name):
    if os.name != "nt":
        pytest.skip("Windows PowerShell startup boundary requires Windows")
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip("The selected PowerShell edition is required")
    backend = tmp_path / "backend"
    backend.mkdir()
    launcher = backend / "start.ps1"
    shutil.copyfile(Path(__file__).resolve().parents[1] / "start.ps1", launcher)
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(launcher)],
        capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
    )
    assert result.returncode == 1
    assert "blocked at dependencies" in result.stdout
    assert ".\\backend\\start.ps1" in result.stdout and "-Guide" in result.stdout
    assert "uv sync --locked" in result.stdout
