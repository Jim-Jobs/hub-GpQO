"""检查/创建 Milvus 集合。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.milvus import ensure_collection  # noqa: E402


def main() -> int:
    settings = get_settings()
    if settings.mock_external:
        print("mock mode: skip milvus collection check")
        return 0
    try:
        ensure_collection()
    except Exception as exc:
        print(f"milvus collection check failed: {exc}")
        return 1
    print(f"collection ready: {settings.milvus_collection}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
