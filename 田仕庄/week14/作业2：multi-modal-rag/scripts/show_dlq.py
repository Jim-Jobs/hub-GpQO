"""查看 Worker 死信队列（JSONL）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Show worker DLQ")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--path", default="")
    args = parser.parse_args()

    path = Path(args.path or get_settings().worker_dlq_path)
    if not path.is_file():
        print(f"no DLQ file: {path}")
        return 0

    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line]
    print(f"DLQ: {path} entries={len(lines)}")
    for line in lines[-max(1, args.limit) :]:
        try:
            record = json.loads(line)
        except ValueError:
            print(line)
            continue
        print(
            f"{record.get('ts', '-')} "
            f"file={record.get('payload', {}).get('id')} "
            f"code={record.get('code')} message={record.get('message')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
