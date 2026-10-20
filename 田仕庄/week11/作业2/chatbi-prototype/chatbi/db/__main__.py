"""命令行入口：python -m chatbi.db [--init] [--list]

默认只打印配置；--init 建表并打印表清单。
"""
from __future__ import annotations

import sys

from .base import Base
from .engine import ORM_DB, init_db, orm_url


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    print(f"ORM 库: {ORM_DB}")
    print(f"URL   : {orm_url()}")

    if "--init" in args:
        init_db()
        print("建表完成（幂等）")
    if "--init" in args or "--list" in args:
        for name in sorted(Base.metadata.tables):
            print(f"  - {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
