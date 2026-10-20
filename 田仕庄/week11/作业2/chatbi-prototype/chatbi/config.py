"""全局配置：数据源路径与语义层目录。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMANTIC_DIR = ROOT / "semantic"
PERMISSIONS_FILE = SEMANTIC_DIR / "permissions.yaml"

# 优先使用工程内置 data/chinook.db，其次回退到外部示例库
_LOCAL_DB = ROOT / "data" / "chinook.db"
_EXTERNAL_DB = Path(
    r"C:\Users\Cooper\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent"
    r"\work-mode-projects\6ad621bc86abd8c7b42c7fd4\chinook.db"
)
DEFAULT_DB = Path(os.environ.get("CHATBI_DB", str(_LOCAL_DB if _LOCAL_DB.exists() else _EXTERNAL_DB)))

# 语义层元数据库（存放版本化对象与审批审计），未建库时运行时自动回退 YAML
META_DB = Path(os.environ.get("CHATBI_META_DB", str(ROOT / "data" / "chatbi_meta.db")))

MAX_ROWS = int(os.environ.get("CHATBI_MAX_ROWS", "100000"))
QUERY_TIMEOUT_S = float(os.environ.get("CHATBI_TIMEOUT", "30"))
