"""测试环境兜底：绝不上报 trace。

如果开发者本机设了 LANGSMITH_TRACING=true，跑单测会把假数据/空载荷上报到云端，
既污染项目又拖慢测试。LangSmith 客户端在首次构造时读取并缓存开关状态，
所以这里用 session 级 autouse fixture 尽早强制关闭。
"""

import os

import pytest


@pytest.fixture(autouse=True, scope="session")
def _disable_langsmith_tracing() -> None:
    for name in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY"):
        os.environ.pop(name, None)
    os.environ["LANGSMITH_TRACING"] = "false"
