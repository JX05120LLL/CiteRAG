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
        return "\n".join(rows)


def dependency_checks(profile: str) -> list[Check]:
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
) -> Report:
    checks = dependency_checks(profile)
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
    checks.append(
        Check(
            "optional_features", "disabled", "文字启动不要求 LiveKit、ASR、TTS、VAD、Agent 或 MCP。"
        )
    )
    return Report(profile, checks)


async def database_checks(value: str) -> list[Check]:
    config = database_config_check(value)
    if config.state == "blocked":
        return [config]
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
    parser.add_argument("--api-port", type=int, default=8000)
    parser.add_argument("--ui-port", type=int, default=5173)
    parser.add_argument("--backend-only", action="store_true")
    args = parser.parse_args()
    if not all(1 <= value <= 65535 for value in (args.api_port, args.ui_port)):
        parser.error("Ports must be between 1 and 65535")
    report = inspect(
        args.profile,
        {"CITERAG_DATABASE_URL": os.environ.get("CITERAG_DATABASE_URL", ""),
         "POSTGRES_WORKSPACE": "present" if os.environ.get("POSTGRES_WORKSPACE") else ""},
        api_port=args.api_port,
        ui_port=args.ui_port,
        frontend=not args.backend_only,
    )
    if args.database and args.profile != "ui":
        report.checks = [
            item for item in report.checks if item.code != "business_database_unchecked"
        ]
        report.checks.extend(
            asyncio.run(database_checks(os.environ.get("CITERAG_DATABASE_URL", "")))
        )
    print(report.render())
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
