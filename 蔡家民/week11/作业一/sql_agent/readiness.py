from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .model import OpenAICompatibleModel


def preflight(root: Path):
    config = json.loads((root / "config/model_example.json").read_text(encoding="utf-8"))
    required = [config[k] for k in ("base_url_env", "api_key_env", "model_env")]
    missing = [name for name in required if not os.environ.get(name)]
    files = ["data/chinook.db", "evaluation/datasets/seed.json", "evaluation/datasets/manifest.json"]
    absent = [name for name in files if not (root / name).is_file()]
    error = ""
    if not missing:
        try:
            OpenAICompatibleModel(root / "config/model_example.json")
        except ValueError as exc:
            error = str(exc)
    return {"python_ok": sys.version_info >= (3, 11), "missing_files": absent,
            "missing_environment": missing, "configuration_error": error,
            "ready_for_live": not missing and not absent and not error and sys.version_info >= (3, 11),
            "network_called": False, "secrets_printed": False}
