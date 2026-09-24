"""Offline, same-host bundle of both PostgreSQL databases and private runtime files.

The API owner lock must be free before the first dump. This is deliberately not
an online snapshot: sequential dumps and file copies are only coherent while
all product writes are stopped.
"""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import asyncpg
from sqlalchemy.engine import make_url

from app.config import LOCAL_RUNTIME_ROOT, PROJECT_ROOT
from app.rag.owner import ApiOwner

FILES = ("business.dump", "engine.dump")


def _connection(url: str) -> dict:
    parsed = make_url(url)
    if (parsed.drivername not in {"postgresql", "postgresql+asyncpg"}
        or parsed.host not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.database or not parsed.username):
        raise ValueError("Backup requires an explicit loopback PostgreSQL database")
    return {"host": parsed.host, "port": parsed.port or 5432,
            "user": parsed.username, "database": parsed.database,
            "password": parsed.password}


def _distinct_databases(business_url: str, engine_url: str) -> None:
    business, engine = _connection(business_url), _connection(engine_url)
    if (business["port"], business["database"]) == (engine["port"], engine["database"]):
        raise ValueError("Business and engine databases must be distinct")


def _tool(pg_bin: Path, name: str) -> Path:
    executable = pg_bin / (name + (".exe" if os.name == "nt" else ""))
    if not executable.is_file():
        raise RuntimeError("Required PostgreSQL client tool is unavailable")
    return executable


def _run_pg(pg_bin: Path, name: str, url: str, args: list[str]) -> None:
    connection = _connection(url)
    env = os.environ.copy()
    env.update({"PGHOST": connection["host"], "PGPORT": str(connection["port"]),
                "PGUSER": connection["user"], "PGDATABASE": connection["database"],
                "PGCONNECT_TIMEOUT": "5"})
    if connection["password"]:
        env["PGPASSWORD"] = connection["password"]
    else:
        env.pop("PGPASSWORD", None)
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        result = subprocess.run([str(_tool(pg_bin, name)), *args], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=flags, timeout=300, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("PostgreSQL backup or restore tool failed") from None
    if result.returncode != 0:
        raise RuntimeError("PostgreSQL backup or restore tool failed")


def _safe_tree(root: Path) -> None:
    if root.is_symlink() or root.is_junction():
        raise RuntimeError("Private runtime path must not be a link")
    for base, dirs, files in os.walk(root):
        for name in [*dirs, *files]:
            item = Path(base) / name
            if item.is_symlink() or item.is_junction():
                raise RuntimeError("Private runtime tree contains a link")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _files(bundle: Path) -> dict[str, str]:
    _safe_tree(bundle)
    return {path.relative_to(bundle).as_posix(): _hash(path)
            for path in bundle.rglob("*") if path.is_file() and path.name != "manifest.json"}


def verify_bundle(bundle: Path) -> bool:
    if bundle.is_symlink() or bundle.is_junction():
        return False
    bundle = bundle.resolve()
    try:
        metadata = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        return (metadata.get("format") == 1 and isinstance(metadata.get("files"), dict)
                and all((bundle / name).is_file() for name in FILES)
                and metadata["files"] == _files(bundle))
    except (OSError, ValueError, TypeError, RuntimeError):
        return False


def _paths(runtime_root: Path, backup_root: Path) -> tuple[Path, Path]:
    runtime, backups = runtime_root.resolve(), backup_root.resolve()
    if (runtime == backups or runtime.is_relative_to(backups)
        or backups.is_relative_to(runtime) or runtime_root.is_symlink()
        or backup_root.is_symlink() or backup_root.is_junction()):
        raise ValueError("Backup and runtime roots must be separate real directories")
    return runtime, backups


async def backup_bundle(business_url: str, engine_url: str, runtime_root: Path,
                        backup_root: Path, pg_bin: Path, *, day: str | None = None,
                        owner_assertion: Callable[[], None] | None = None) -> Path:
    _distinct_databases(business_url, engine_url)
    runtime, backups = _paths(runtime_root, backup_root)
    if not runtime.is_dir():
        raise RuntimeError("Private runtime directory is missing")
    await asyncio.to_thread(_safe_tree, runtime)
    backups.mkdir(parents=True, exist_ok=True)
    today = day or datetime.now().date().isoformat()
    if datetime.strptime(today, "%Y-%m-%d").date().isoformat() != today:
        raise ValueError("Backup day must be ISO format")
    destination = backups / today
    if destination.exists():
        if verify_bundle(destination):
            return destination
        raise RuntimeError("Existing daily backup failed verification")
    stage = backups / ("." + uuid4().hex + ".partial")
    stage.mkdir()
    try:
        async def capture():
            await asyncio.to_thread(_run_pg, pg_bin, "pg_dump", business_url,
                                    ["--format=custom", "--file", str(stage / FILES[0])])
            await asyncio.to_thread(_run_pg, pg_bin, "pg_dump", engine_url,
                                    ["--format=custom", "--file", str(stage / FILES[1])])
            await asyncio.to_thread(shutil.copytree, runtime, stage / "runtime")

        if owner_assertion is None:
            # Offline CLI holds the owner lock to exclude API startup.
            async with ApiOwner(business_url):
                await capture()
        else:
            # The application runner holds the owner lock and BackupGate.
            owner_assertion()
            await capture()
            owner_assertion()
        metadata = {"format": 1, "created_at": datetime.now().astimezone().isoformat(),
                    "files": await asyncio.to_thread(_files, stage)}
        (stage / "manifest.json").write_text(json.dumps(metadata, sort_keys=True),
                                             encoding="utf-8")
        if not await asyncio.to_thread(verify_bundle, stage):
            raise RuntimeError("Created backup failed verification")
        stage.rename(destination)
        completed = []
        for path in backups.iterdir():
            if (path.is_dir() and path.name[:1].isdigit()
                and await asyncio.to_thread(verify_bundle, path)):
                completed.append(path)
        completed.sort()
        for old in completed[:-7]:
            if old.resolve().parent != backups or old.is_symlink() or old.is_junction():
                raise RuntimeError("Unsafe old backup path")
            shutil.rmtree(old)
        return destination
    finally:
        if stage.exists():
            if stage.resolve().parent != backups or stage.is_symlink() or stage.is_junction():
                raise RuntimeError("Unsafe partial backup path")
            shutil.rmtree(stage)


async def _empty_database(url: str) -> bool:
    connection = await asyncpg.connect(**_connection(url), timeout=5)
    try:
        count = await connection.fetchval("""
            SELECT count(*) FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
            AND relkind IN ('r','p','v','m','S')
        """)
        return count == 0
    finally:
        await connection.close()


async def _block_restored_knowledge(url: str) -> None:
    connection = await asyncpg.connect(**_connection(url), timeout=5)
    try:
        if await connection.fetchval("SELECT to_regclass('public.knowledge_bases')") is not None:
            # A backup may predate a deletion. Never reopen old engine content or
            # citations until the deletion record and source manifest are checked.
            await connection.execute("""
                UPDATE knowledge_bases SET status = 'blocked'
                WHERE local_owner_id IS NOT NULL AND status <> 'blocked'
            """)
    finally:
        await connection.close()


async def restore_bundle(bundle: Path, business_url: str, engine_url: str,
                         runtime_root: Path, pg_bin: Path) -> None:
    if not verify_bundle(bundle):
        raise RuntimeError("Backup bundle failed verification")
    _distinct_databases(business_url, engine_url)
    if runtime_root.exists() and (not runtime_root.is_dir() or any(runtime_root.iterdir())):
        raise RuntimeError("Restore runtime directory is not empty")
    if runtime_root.is_symlink() or runtime_root.is_junction():
        raise RuntimeError("Restore runtime path is a link")
    async with ApiOwner(business_url):
        if not await _empty_database(business_url) or not await _empty_database(engine_url):
            raise RuntimeError("Restore database is not empty")
        await asyncio.to_thread(_run_pg, pg_bin, "pg_restore", business_url,
                                ["--exit-on-error", "--no-owner", "--no-acl", "--dbname",
                                 _connection(business_url)["database"],
                                 str(bundle / FILES[0])])
        await asyncio.to_thread(_run_pg, pg_bin, "pg_restore", engine_url,
                                ["--exit-on-error", "--no-owner", "--no-acl", "--dbname",
                                 _connection(engine_url)["database"],
                                 str(bundle / FILES[1])])
        await asyncio.to_thread(shutil.copytree, bundle / "runtime", runtime_root,
                                dirs_exist_ok=runtime_root.exists())
        await _block_restored_knowledge(business_url)


def main() -> None:
    parser = argparse.ArgumentParser(description="Private, local CiteRAG backup maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup", help="snapshot a stopped local installation")
    backup.add_argument("--pg-bin", type=Path, required=True)
    backup.add_argument("--runtime-root", type=Path, default=LOCAL_RUNTIME_ROOT)
    backup.add_argument("--backup-root", type=Path, default=PROJECT_ROOT / ".local" / "backups")
    verify = commands.add_parser("verify", help="check every file hash in a bundle")
    verify.add_argument("bundle", type=Path)
    restore = commands.add_parser("restore", help="restore only to fresh empty databases")
    restore.add_argument("bundle", type=Path)
    restore.add_argument("--pg-bin", type=Path, required=True)
    restore.add_argument("--runtime-root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "verify":
        if not verify_bundle(args.bundle):
            raise SystemExit("Backup verification failed")
        print("Backup verification passed")
        return
    if args.command == "backup":
        business = os.environ.get("CITERAG_DATABASE_URL")
        engine = os.environ.get("CITERAG_BACKUP_ENGINE_URL")
        if not business or not engine:
            raise SystemExit("Both database URLs must be supplied privately in the environment")
        asyncio.run(backup_bundle(business, engine, args.runtime_root,
                                  args.backup_root, args.pg_bin))
        print("Offline backup completed and verified")
        return
    business = os.environ.get("CITERAG_RESTORE_BUSINESS_URL")
    engine = os.environ.get("CITERAG_RESTORE_ENGINE_URL")
    if not business or not engine:
        raise SystemExit("Fresh restore database URLs must be supplied privately")
    asyncio.run(restore_bundle(args.bundle, business, engine, args.runtime_root, args.pg_bin))
    print("Restore completed; owned knowledge bases remain blocked pending reconciliation")


if __name__ == "__main__":
    main()
