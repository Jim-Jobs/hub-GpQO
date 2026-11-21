import os
from pathlib import Path

from dotenv import load_dotenv

# Resolve the config relative to this project, not the shell's working directory.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=False)


def model_name() -> str:
    return os.getenv("OPENAI_MODEL", "qwen3.8-flash")


def openai_base_url() -> str | None:
    return os.getenv("OPENAI_BASE_URL") or None
