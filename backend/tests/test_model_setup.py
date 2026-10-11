"""Public Windows setup uses synthetic keys and temporary files, never suppliers.

DPAPI and Windows ACL round trips are skipped on Linux rather than represented
as supplier or Windows verification. Public CI can run this file on Windows.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.credentials import load_dashscope_config

SETUP = Path(__file__).resolve().parents[1] / "configure-model.ps1"
BUSINESS_SETUP = SETUP.with_name("business-config.ps1")
SYNTHETIC_KEY = "public-synthetic-not-a-provider-key"
EXPECTED_MODELS = {
    "answer": "qwen-flash",
    "engine": "qwen-plus",
    "summary": "qwen-max",
    "embedding": "text-embedding-v4",
    "rerank": "qwen3-vl-rerank",
}


def ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=["powershell.exe", "pwsh.exe"])
def powershell(request):
    if os.name != "nt":
        pytest.skip("Windows DPAPI and DACL behavior requires a Windows host")
    executable = shutil.which(request.param)
    if executable is None:
        pytest.skip(f"{request.param} is not installed")
    return executable


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "synthetic project"
    backend = root / "backend"
    backend.mkdir(parents=True)
    if SETUP.is_file():
        shutil.copyfile(SETUP, backend / SETUP.name)
    return root


def run_script(powershell, project, body):
    harness = project / "synthetic harness.ps1"
    harness.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        + "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        + f". {ps_literal(project / 'backend' / SETUP.name)} -LibraryOnly\n"
        + body,
        encoding="utf-8-sig",
    )
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("CITERAG_", "PG", "POSTGRES", "DASHSCOPE_"))
        and name.casefold() != "psmodulepath"
    }
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(harness)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, env=environment, check=False,
    )
    assert SYNTHETIC_KEY not in result.stdout + result.stderr
    return result


def save_body(project, workspace="synthetic_workspace", key=SYNTHETIC_KEY):
    return (
        f"$key = ConvertTo-SecureString {ps_literal(key)} -AsPlainText -Force\n"
        "try {\n"
        f"  Save-CiteRAGTextModelConfiguration -ProjectRoot {ps_literal(project)} "
        f"-WorkspaceId {ps_literal(workspace)} -ApiKey $key\n"
        "} finally { $key.Dispose() }\n"
    )


def test_first_time_record_is_private_dpapi_and_exact_loader_compatible(powershell, project):
    assert SETUP.is_file(), "The public first-time model setup entry is missing"
    result = run_script(powershell, project, save_body(project))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "saved (not verified)" in result.stdout
    target = project / ".local/runtime/models/dashscope.credential.xml"
    assert SYNTHETIC_KEY.encode() not in target.read_bytes()
    config = load_dashscope_config(target)
    assert config.api_key.get_secret_value() == SYNTHETIC_KEY
    assert config.workspace_id == "synthetic_workspace"
    assert config.region == "cn-beijing"
    assert config.embedding_dimension == 1024
    assert config.models == EXPECTED_MODELS
    assert sorted(path.name for path in target.parent.iterdir()) == [target.name]


def test_existing_record_is_never_read_or_replaced(powershell, project):
    directory = project / ".local/runtime/models"
    directory.mkdir(parents=True)
    target = directory / "dashscope.credential.xml"
    original = b"synthetic invalid record; must not be parsed or replaced"
    target.write_bytes(original)
    result = run_script(powershell, project, save_body(project))
    assert result.returncode != 0
    assert "already exists" in result.stdout + result.stderr
    assert target.read_bytes() == original
    assert sorted(path.name for path in directory.iterdir()) == [target.name]


@pytest.mark.parametrize("workspace", ["", "bad workspace", "../other", "bad\nworkspace"])
def test_invalid_workspace_does_not_create_configuration(powershell, project, workspace):
    result = run_script(powershell, project, save_body(project, workspace))
    assert result.returncode != 0
    assert "Invalid Workspace ID" in result.stdout + result.stderr
    assert not (project / ".local").exists()


def test_empty_key_does_not_create_configuration(powershell, project):
    body = (
        "$key = [Security.SecureString]::new()\n"
        "try {\n"
        f"  Save-CiteRAGTextModelConfiguration -ProjectRoot {ps_literal(project)} "
        "-WorkspaceId 'synthetic_workspace' -ApiKey $key\n"
        "} finally { $key.Dispose() }\n"
    )
    result = run_script(powershell, project, body)
    assert result.returncode != 0
    assert "API Key must not be empty" in result.stdout + result.stderr
    assert not (project / ".local").exists()


@pytest.mark.parametrize("exists", [False, True])
def test_check_only_never_prompts_reads_or_writes(powershell, project, exists):
    target = project / ".local/runtime/models/dashscope.credential.xml"
    if exists:
        target.parent.mkdir(parents=True)
        target.write_bytes(b"synthetic deliberately invalid record")
    before = {str(path.relative_to(project)): path.read_bytes()
              for path in project.rglob("*") if path.is_file()}
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(project / "backend" / SETUP.name), "-CheckOnly"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ("saved (not verified)" if exists else "not saved") in result.stdout
    after = {str(path.relative_to(project)): path.read_bytes()
             for path in project.rglob("*") if path.is_file()}
    assert after == before


def test_redirected_configuration_directory_is_rejected(powershell, project, tmp_path):
    outside = tmp_path / "synthetic outside"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("public synthetic preservation marker", encoding="utf-8")
    body = (
        f"New-Item -ItemType Junction -Path {ps_literal(project / '.local')} "
        f"-Target {ps_literal(outside)} | Out-Null\n"
        + save_body(project)
    )
    result = run_script(powershell, project, body)
    assert result.returncode != 0
    assert "Redirected configuration paths" in result.stdout + result.stderr
    assert marker.read_text(encoding="utf-8") == "public synthetic preservation marker"
    assert sorted(path.name for path in outside.iterdir()) == [marker.name]


def test_concurrent_creation_preserves_the_other_record(powershell, project):
    target = project / ".local/runtime/models/dashscope.credential.xml"
    body = (
        "function Export-Clixml {\n"
        "  param($LiteralPath, $Encoding, $Depth, [switch]$NoClobber)\n"
        "  $input | Microsoft.PowerShell.Utility\\Export-Clixml -LiteralPath $LiteralPath "
        "-Encoding $Encoding -Depth $Depth -NoClobber\n"
        f"  [IO.File]::WriteAllText({ps_literal(target)}, 'synthetic concurrent record')\n"
        "}\n"
        + save_body(project)
    )
    result = run_script(powershell, project, body)
    assert result.returncode != 0
    assert "configuration was not saved" in result.stdout + result.stderr
    assert target.read_text(encoding="utf-8") == "synthetic concurrent record"


def business_record_body(project, override=None, *, private=True):
    """Build a synthetic DPAPI fixture; it never connects to PostgreSQL."""
    shutil.copyfile(BUSINESS_SETUP, project / "backend" / BUSINESS_SETUP.name)
    target = project / ".local/runtime/business-postgres/credential.xml"
    metadata = {
        "Provider": "postgresql", "Host": "127.0.0.1", "Port": 55433,
        "Database": "assistant_app", "Username": "citerag_app",
        "Container": "citerag-rag-postgres",
    }
    if override:
        metadata.update(override)
    fields = "; ".join(
        f"{name} = {value if isinstance(value, int) else ps_literal(value)}"
        for name, value in metadata.items()
    )
    body = (
        f". {ps_literal(project / 'backend' / BUSINESS_SETUP.name)}\n"
        f"$directory = {ps_literal(target.parent)}\n"
        "New-Item -ItemType Directory -Path $directory -Force | Out-Null\n"
        "Set-CiteRAGModelSetupAcl $directory $true\n"
        f"$key = ConvertTo-SecureString {ps_literal(SYNTHETIC_KEY)} -AsPlainText -Force\n"
        f"$record = [pscustomobject]@{{ {fields}; "
        "Credential = [Management.Automation.PSCredential]::new('citerag_app', $key) }\n"
        f"$record | Export-Clixml -LiteralPath {ps_literal(target)} -Depth 5 -NoClobber\n"
    )
    if private:
        body += f"Set-CiteRAGModelSetupAcl {ps_literal(target)} $false\n"
    return target, body


def test_business_config_compatible_fixture_does_not_print_connection(powershell, project):
    target, body = business_record_body(project)
    body += (
        f"try {{ $url = Get-CiteRAGBusinessUrl {ps_literal(target)}\n"
        f"  if ($url -ne ('postgresql+asyncpg://citerag_app:' + {ps_literal(SYNTHETIC_KEY)} "
        "+ '@127.0.0.1:55433/assistant_app')) { throw 'incorrect synthetic result' }\n"
        "  Write-Output 'compatible synthetic configuration'\n"
        "} finally { $key.Dispose(); $url = $null }\n"
    )
    result = run_script(powershell, project, body)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "compatible synthetic configuration"


@pytest.mark.parametrize("override", [
    {"Provider": "other"}, {"Host": "127.0.0.2"}, {"Port": 55432},
    {"Database": "assistant_rag"}, {"Username": "postgres"},
    {"Container": "legacy-test-postgres"},
])
def test_business_config_rejects_wrong_target_without_secret(powershell, project, override):
    target, body = business_record_body(project, override)
    body += (
        f"try {{ Get-CiteRAGBusinessUrl {ps_literal(target)} | Out-Null }}\n"
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 2 }\n"
        "finally { $key.Dispose() }\n"
    )
    result = run_script(powershell, project, body)
    assert result.returncode == 2
    assert "Controlled business configuration could not be loaded" in result.stderr
    assert "postgresql+asyncpg://" not in result.stdout + result.stderr


def test_business_config_rejects_unprotected_record(powershell, project):
    target, body = business_record_body(project, private=False)
    body += (
        f"try {{ Get-CiteRAGBusinessUrl {ps_literal(target)} | Out-Null }}\n"
        "catch { [Console]::Error.WriteLine($_.Exception.Message); exit 2 }\n"
        "finally { $key.Dispose() }\n"
    )
    result = run_script(powershell, project, body)
    assert result.returncode == 2
    assert "Controlled business configuration could not be loaded" in result.stderr
