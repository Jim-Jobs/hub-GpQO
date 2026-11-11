"""生成并注册知识库 API Key。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.api.deps import hash_api_key  # noqa: E402
from app.db import Session, init_db  # noqa: E402
from app.models import KbApiKey  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Register a KB API key")
    parser.add_argument("knowledge_base_id")
    parser.add_argument("api_key")
    parser.add_argument("--role", default="write", choices=["read", "write", "admin"])
    args = parser.parse_args()

    init_db()
    with Session() as session:
        existing = (
            session.query(KbApiKey)
            .filter(KbApiKey.api_key_hash == hash_api_key(args.api_key))
            .first()
        )
        if existing is not None:
            print(f"key already exists: id={existing.id}")
            return 0
        row = KbApiKey(
            api_key_hash=hash_api_key(args.api_key),
            knowledge_base_id=args.knowledge_base_id,
            role=args.role,
        )
        session.add(row)
        session.flush()
        key_id = row.id
        session.commit()

    print(f"registered key id={key_id} kb={args.knowledge_base_id} role={args.role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
