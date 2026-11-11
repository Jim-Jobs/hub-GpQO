"""把 FastAPI 的 OpenAPI 契约导出到 docs/api/。"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMP_DATA = ROOT / "tests" / ".runtime" / "openapi-export"

os.environ["RAG_MOCK_EXTERNAL"] = "1"
os.environ["RAG_PARSE_BACKEND"] = "mock"
os.environ["RAG_DATA_DIR"] = str(TEMP_DATA)
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402


def main() -> int:
    spec = app.openapi()
    output_dir = ROOT / "docs" / "api"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        import yaml

        text = yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)
        path = output_dir / "openapi.yaml"
    except ImportError:
        text = json.dumps(spec, ensure_ascii=False, indent=2)
        path = output_dir / "openapi.json"

    path.write_text(text, encoding="utf-8")
    shutil.rmtree(TEMP_DATA, ignore_errors=True)
    print(f"exported {path} ({len(spec.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
