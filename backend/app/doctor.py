"""Read-only, secret-free startup diagnostics; never import the application lifespan."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import os
import shutil
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import EXPECTED_SCHEMA_REVISION

ROOT = Path(__file__).resolve().parents[2]
PROFILES = ("ui", "text", "knowledge")


@dataclass(frozen=True)
class Check:
    code: str
    state: str
    detail: str


@dataclass
class Report:
    profile: str
    checks: list[Check]

    @property
    def exit_code(self) -> int:
        if any(item.state == "blocked" for item in self.checks):
            return 1
        return 2 if any(item.code == "business_database_unchecked" for item in self.checks) else 0

    @property
    def summary(self) -> str:
        if self.exit_code == 1:
            return "启动缺项，未初始化或迁移数据库，未启动服务。"
        if self.exit_code == 2:
            return "静态配置检查完成；业务数据库尚未检查，未启动服务。"
        if self.profile == "ui":
            return "界面/API 启动条件检查完成；聊天、入库和模型功能未验收。"
        return "本地文字启动检查完成；模型、检索质量及业务验收仍未验证。"

    def render(self) -> str:
        rows = [f"CiteRAG [{self.profile}] — {self.summary}"]
        rows.extend(f"[{item.state}] {item.code}: {item.detail}" for item in self.checks)
        blocked = {item.code for item in self.checks if item.state == "blocked"}
        if blocked:
            rows.append("下一步（从仓库根目录执行；以下命令仅供手动选择，本诊断不会执行）：")
            install = "uv sync --locked"
            if self.profile == "knowledge":
                install += " --extra rag"
            if any(item.code.startswith("agent_") for item in self.checks):
                install += " --extra agent"
            advice = {
                "python": "先准备 Python 3.12 和 uv；后端环境使用下方显式安装步骤。",
                "backend_dependencies": (
                    "显式安装（保留需要的可选 extras）：\n"
                    f"  Set-Location .\\backend\n  {install}\n  Set-Location .."
                ),
                "frontend_dependencies": (
                    "显式安装前端依赖：\n  Set-Location .\\frontend\n"
                    "  pnpm install --frozen-lockfile\n  Set-Location .."
                ),
                "node": "先安装 Node.js 22.12+，再运行 node --version 核对。",
                "database_not_configured": (
                    "先准备独立业务库与受控 CITERAG_DATABASE_URL，"
                    "不在命令参数中粘贴连接或密码。查看准备步骤：\n"
                    "  Get-Content -LiteralPath .\\backend\\STARTUP.md"
                ),
                "model_record": (
                    "先检查保存状态：\n  powershell.exe -NoProfile -ExecutionPolicy Bypass "
                    "-File .\\backend\\configure-model.ps1 -CheckOnly\n"
                    "  首次缺记录时按启动文档使用 "
                    "-WorkspaceId YOUR_DASHSCOPE_WORKSPACE_ID；须替换为真实北京地域工作空间 ID，"
                    "交互输入 Key；不覆盖已有记录。"
                ),
                "rag_record": (
                    "核对独立引擎库配置：\n  Get-Content -LiteralPath .\\backend\\README.md"
                ),
                "database_schema_mismatch": (
                    "先停写并成套备份、确认升级授权与恢复方案，不绕过 schema 门禁。核对步骤：\n"
                    "  Get-Content -LiteralPath .\\backend\\STARTUP.md"
                ),
                "unfinished_ingestion": "先审查未完成任务及费用授权；诊断不恢复、重试或修改任务。",
            }
            for code, detail in advice.items():
                if code in blocked:
                    rows.append(f"- {detail}")
            if any(code.startswith("agent_") for code in blocked):
                rows.append(
                    "- Agent 检查点初始化/升级须先停写、备份并取得授权，不自动 setup 或迁移。"
                    "核对步骤：\n  Get-Content -LiteralPath .\\backend\\AGENT.md\n"
                    "  仅缺依赖时，在 backend 中显式安装：\n  " + install
                )
            profile = "knowledge" if self.profile == "knowledge" else "text"
            rows.append(
                "- 查看只读配置说明：\n  powershell.exe -NoProfile -ExecutionPolicy Bypass "
                f"-File .\\backend\\start.ps1 -Profile {profile} -Guide"
            )
        return "\n".join(rows)


def _agent_dependency_check() -> Check:
    missing = []
    for name in ("langgraph", "langgraph-checkpoint-postgres", "psycopg", "psycopg-pool"):
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    return Check(
        "agent_dependencies_missing" if missing else "agent_dependencies",
        "blocked" if missing else "ready",
        "缺少 Agent 依赖；先按锁文件显式安装 --extra agent，不自动安装。"
        if missing else "Agent 依赖元数据存在；未运行任务或初始化检查点。",
    )


def dependency_checks(profile: str, *, agent: bool = False) -> list[Check]:
    checks = [
        Check(
            "python",
            "ready" if sys.version_info[:2] == (3, 12) else "blocked",
            "后端要求 Python 3.12；日常启动使用已安装的 .venv。",
        )
    ]
    packages = ["fastapi", "uvicorn", "sqlalchemy", "asyncpg"]
    if profile == "knowledge":
        packages.append("lightrag-hku")
    missing = []
    for name in packages:
        try:
            importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            missing.append(name)
    checks.append(
        Check(
            "backend_dependencies",
            "blocked" if missing else "ready",
            "缺少依赖：" + ", ".join(missing)
            if missing
            else "所需后端包已安装；本检查不安装或同步依赖。",
        )
    )
    if agent:
        checks.append(_agent_dependency_check())
    return checks


def port_check(code: str, port: int) -> Check:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if os.name == "nt":
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
    except OSError:
        return Check(
            code, "blocked", f"回环端口 {port} 已占用或不可绑定；核对归属，不停止未知进程。"
        )
    return Check(code, "ready", f"回环端口 {port} 可绑定；启动时仍可能发生端口竞争。")


def file_check(code: str, path: Path) -> Check:
    try:
        for ancestor in [path, *path.parents]:
            metadata = ancestor.lstat()
            if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
                return Check(code, "blocked", "配置路径存在重解析点；请核对受控存储。")
        if not stat.S_ISREG(path.lstat().st_mode):
            return Check(code, "blocked", "受控配置不是普通文件。")
    except FileNotFoundError:
        return Check(
            code, "blocked", "受控配置文件缺失；请按 STARTUP.md 准备，不回退到旧测试配置。"
        )
    except OSError:
        return Check(code, "blocked", "配置文件元数据不可读；请核对路径权限。")
    return Check(code, "unverified", "文件存在；未读取凭证内容，尚未验证格式、权限或供应商功能。")


def database_config_check(value: str | None) -> Check:
    if not value:
        return Check(
            "database_not_configured",
            "blocked",
            "业务连接未注入；核对受控配置。初始化或迁移须先备份并取得授权。",
        )
    try:
        url = make_url(value)
        if (
            url.drivername != "postgresql+asyncpg"
            or url.host not in {"127.0.0.1", "localhost", "::1"}
            or not url.database
            or not url.username
            or url.query
            or url.database == "assistant_rag"
            or (url.port is not None and not 1 <= url.port <= 65535)
        ):
            raise ValueError("Unsupported business connection")
    except (ArgumentError, ValueError, TypeError):
        return Check(
            "database_configuration_invalid",
            "blocked",
            "业务连接须为明确的回环 asyncpg URL，且与索引库分开；不显示连接内容。",
        )
    return Check("database_configuration", "ready", "业务连接格式符合本地约束；未显示连接内容。")


def inspect(
    profile: str,
    environment: dict[str, str],
    root: Path = ROOT,
    *,
    api_port: int = 8000,
    ui_port: int = 5173,
    frontend: bool = True,
    agent: bool = False,
) -> Report:
    checks = dependency_checks(profile, agent=True) if agent else dependency_checks(profile)
    if environment.get("POSTGRES_WORKSPACE") or (root / "backend/config.ini").exists():
        checks.append(Check(
            "legacy_engine_configuration", "blocked",
            "发现不兼容的旧引擎配置；核对 POSTGRES_WORKSPACE 或 backend/config.ini，不自动删除。",
        ))
    checks.append(port_check("api_port", api_port))
    if frontend:
        checks.append(port_check("ui_port", ui_port))
        checks.append(
            Check(
                "frontend_dependencies",
                "ready"
                if (root / "frontend/node_modules/vite/bin/vite.js").is_file()
                else "blocked",
                "前端需已安装锁定依赖；首次安装见 STARTUP.md。",
            )
        )
        node = shutil.which("node")
        valid_node = False
        if node:
            try:
                version = subprocess.run(
                    [node, "--version"], capture_output=True, text=True, timeout=5, check=False
                )
                parts = tuple(
                    int(item) for item in version.stdout.strip().removeprefix("v").split(".")
                )
                valid_node = version.returncode == 0 and parts >= (22, 12, 0)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        checks.append(
            Check("node", "ready" if valid_node else "blocked", "前端要求 Node.js 22.12+。")
        )
    if profile == "ui":
        checks.append(
            Check(
                "business_functions", "disabled",
                "仅检查界面/API 条件；本诊断不执行聊天、入库或探测。"
            )
        )
        return Report(profile, checks)
    database = database_config_check(environment.get("CITERAG_DATABASE_URL"))
    checks.append(database)
    if database.state == "ready":
        checks.append(
            Check(
                "business_database_unchecked",
                "unverified",
                "加 --database 显式执行有超时的只读表结构、归属和待处理任务检查。",
            )
        )
    checks.append(
        file_check("model_record", root / ".local/runtime/models/dashscope.credential.xml")
    )
    if profile == "knowledge":
        checks.append(file_check("rag_record", root / ".local/runtime/rag-postgres/credential.xml"))
    checks.append(Check(
        "optional_features", "disabled",
        "Agent 为本次显式选项；本检查不启动语音、MCP 或天气工具。"
        if agent else "文字启动不要求 LiveKit、ASR、TTS、VAD、Agent 或 MCP。",
    ))
    return Report(profile, checks)


async def _agent_checkpoint_checks(connection, expected_version: int) -> Check:
    from app.agent.checkpoints import checkpoint_namespace

    try:
        schema = await connection.scalar(text("SELECT current_schema()"))
        namespace = checkpoint_namespace(schema) if isinstance(schema, str) and schema else None
        if namespace is None or await connection.scalar(
            text("SELECT to_regnamespace(:name)"), {"name": namespace}
        ) is None:
            return Check(
                "agent_checkpoint_namespace_missing", "blocked",
                "Agent 检查点命名空间缺失；先停写备份并确认已有 schema 迁移，不自动创建。",
            )
        if not await connection.scalar(
            text("SELECT has_schema_privilege(:name, 'USAGE')"), {"name": namespace}
        ):
            return Check(
                "agent_checkpoint_permissions_missing", "blocked",
                "Agent 检查点 schema 缺少 USAGE 权限；请由维护者核对业务角色的最小权限，"
                "不自动授权或修改结构。",
            )
        # Saver reads all data tables, upserts checkpoints/writes, inserts blobs,
        # and retention deletes all three. Migration metadata is read-only at runtime.
        table_permissions = {
            "checkpoints": ("SELECT", "INSERT", "UPDATE", "DELETE"),
            "checkpoint_blobs": ("SELECT", "INSERT", "DELETE"),
            "checkpoint_writes": ("SELECT", "INSERT", "UPDATE", "DELETE"),
            "checkpoint_migrations": ("SELECT",),
        }
        for table, permissions in table_permissions.items():
            if await connection.scalar(
                text("SELECT to_regclass(:name)"), {"name": f"{namespace}.{table}"}
            ) is None:
                return Check(
                    "agent_checkpoint_tables_missing", "blocked",
                    "Agent 检查点表缺失；先备份并获得初始化授权，再显式运行 "
                    "python -m app.agent.checkpoints。本诊断不建表。",
                )
            # A comma-separated privilege argument means ANY, not ALL, in PostgreSQL.
            # Combine fixed individual predicates; do not test permissions by writing.
            predicate = " AND ".join(
                f"has_table_privilege(:name, '{permission}')" for permission in permissions
            )
            if not await connection.scalar(
                text("SELECT " + predicate), {"name": f"{namespace}.{table}"}
            ):
                return Check(
                    "agent_checkpoint_permissions_missing", "blocked",
                    f"Agent 检查点 {table} 运行/清理权限不足；请由维护者核对业务角色的 "
                    f"{'/'.join(permissions)} 最小权限，不自动授权或试写。",
                )
        # Namespace is public's fixed name or a server-derived hexadecimal hash.
        version = await connection.scalar(text(
            f'SELECT max(v) FROM "{namespace}".checkpoint_migrations'
        ))
        if version != expected_version:
            return Check(
                "agent_checkpoint_version_mismatch", "blocked",
                "检查点版本与已安装的锁定依赖不匹配；先核对依赖、停写备份并确认升级，"
                "不自动 setup 或迁移。",
            )
        return Check(
            "agent_checkpoints", "ready",
            "Agent 命名空间、检查点表、运行/清理权限和迁移版本只读检查通过；"
            "未启动应用或执行任务。",
        )
    except Exception:  # noqa: BLE001 - checkpoint/driver diagnostics can contain private values
        return Check(
            "agent_checkpoint_read_failed", "blocked",
            "Agent 检查点元数据读取失败；核对本地表结构与读取权限，未初始化或迁移。",
        )


async def database_checks(value: str, *, agent: bool = False) -> list[Check]:
    config = database_config_check(value)
    if config.state == "blocked":
        return [config]
    checkpoint_version = None
    if agent:
        dependency = _agent_dependency_check()
        if dependency.state == "blocked":
            return [dependency]
        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            checkpoint_version = len(AsyncPostgresSaver.MIGRATIONS) - 1
        except Exception:  # noqa: BLE001 - optional package errors must not leak local details
            return [Check(
                "agent_dependencies_unavailable", "blocked",
                "Agent 可选依赖无法加载；按锁文件显式检查 --extra agent 安装，不自动修复。",
            )]
    engine = None
    result = []
    try:
        engine = create_async_engine(
            value,
            hide_parameters=True,
            echo=False,
            connect_args={
                "timeout": 5,
                "command_timeout": 5,
                "server_settings": {"default_transaction_read_only": "on"},
            },
        )
        async with engine.connect() as connection:
            await connection.scalar(text("SELECT 1"))
            revisions = list(
                await connection.scalars(text("SELECT version_num FROM alembic_version LIMIT 3"))
            )
            if revisions != [EXPECTED_SCHEMA_REVISION]:
                result = [
                    Check(
                        "database_schema_mismatch",
                        "blocked",
                        f"业务 schema 须为 {EXPECTED_SCHEMA_REVISION}；先备份并确认迁移。",
                    )
                ]
            elif await connection.scalar(text("SELECT count(*) FROM local_profiles")) != 1:
                result = [
                    Check(
                        "database_owner_invalid",
                        "blocked",
                        "本地归属记录须唯一；未创建或认领记录。",
                    )
                ]
            elif await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM ingestion_jobs "
                    "WHERE status IN ('queued', 'running'))"
                )
            ):
                result = [
                    Check(
                        "unfinished_ingestion",
                        "blocked",
                        "存在待处理入库任务；先审查任务和费用授权，不自动恢复或修改任务。",
                    )
                ]
            else:
                result = [
                    Check(
                        "business_database",
                        "ready",
                        "只读连接、当前 schema、唯一归属和待处理任务检查通过；未运行应用清理。",
                    )
                ]
                if agent:
                    result.append(await _agent_checkpoint_checks(connection, checkpoint_version))
    except Exception:  # noqa: BLE001 - sanitize all driver details at this CLI boundary
        result = [
            Check(
                "database_read_failed",
                "blocked",
                "业务连接、表结构或读取权限检查失败；核对服务和受控配置，未执行迁移。",
            )
        ]
    finally:
        if engine is not None:
            try:
                await engine.dispose()
            except Exception:  # noqa: BLE001 - never expose driver/DSN details
                result = [
                    Check("database_close_failed", "blocked", "只读连接释放失败；请核对本地服务。")
                ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, default="ui")
    parser.add_argument(
        "--database", action="store_true", help="explicit read-only business DB check"
    )
    parser.add_argument("--agent", action="store_true", help="opt-in read-only Agent preflight")
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int, default=5173)
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()
    if not all(1 <= value <= 65535 for value in (args.api_port, args.ui_port)):
        parser.error("Ports must be between 1 and 65535")
    if args.agent and args.profile == "ui":
        parser.error("--agent requires the text or knowledge profile")
    report = inspect(
        args.profile,
        {"CITERAG_DATABASE_URL": os.environ.get("CITERAG_DATABASE_URL", ""),
         "POSTGRES_WORKSPACE": "present" if os.environ.get("POSTGRES_WORKSPACE") else ""},
        api_port=args.api_port,
        ui_port=args.ui_port,
        frontend=not args.backend_only,
        agent=args.agent,
    )
    if args.database and args.profile != "ui":
        report.checks = [
            item for item in report.checks if item.code != "business_database_unchecked"
        ]
        report.checks.extend(
            asyncio.run(database_checks(os.environ.get("CITERAG_DATABASE_URL", ""),
                                        agent=args.agent))
        )
    print(report.render())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
