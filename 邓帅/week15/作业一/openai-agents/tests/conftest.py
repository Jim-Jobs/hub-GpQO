"""openai-agents 套测试共用：sys.path 注入 + pytest 配置说明。"""

from __future__ import annotations

import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
for sub in (SUITE, SUITE / "sentiment-service", SUITE / "respond-service", SUITE / "worker"):
    s = str(sub)
    if s not in sys.path:
        sys.path.insert(0, s)
