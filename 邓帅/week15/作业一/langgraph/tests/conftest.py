"""langgraph 套测试共用：sys.path 注入套根。"""

from __future__ import annotations

import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
s = str(SUITE)
if s not in sys.path:
    sys.path.insert(0, s)
