"""OpenAI Agents SDK 版编排器入口（端口 9000）。

编排逻辑本身是框架无关的，由 ``backends.shared.orchestrator`` 提供；这里只负责
绑定 OpenAI Agents SDK 这一套的端口、Agent 地址与数据库。
"""

from __future__ import annotations

import uvicorn

from backends.shared.config.profiles import get_profile
from backends.shared.orchestrator.factory import create_app

PROFILE = get_profile("openai_agents")
app = create_app(PROFILE)


if __name__ == "__main__":
    uvicorn.run(
        "backends.openai_agents.orchestrator.main:app",
        host="127.0.0.1",
        port=PROFILE.orchestrator_port,
    )
