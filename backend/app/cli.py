"""Retired account bootstrap entry point; no database or password operations."""

import sys


def main() -> int:
    print(
        "CiteRAG 本地单用户版无需创建管理员。配置数据库后请运行 alembic upgrade head。",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
