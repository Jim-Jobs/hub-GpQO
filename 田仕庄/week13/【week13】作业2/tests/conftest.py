"""pytest 公共夹具。"""

import pytest

from sql_agent import config
from sql_agent.db_parser import DBParser


@pytest.fixture(scope="session")
def parser() -> DBParser:
    """全 session 共享一个 DBParser（元数据解析开销只付一次）。"""
    return DBParser()


@pytest.fixture(autouse=True)
def _review_flag(request, monkeypatch):
    """评审回路开关的测试策略：

    - 带 `query_review` 标记的单测：显式开启（测 Reflection 回路）
    - 带 `optimize_always` 标记：强制 OPTIMIZE_STRATEGY=always（保持旧拓扑的脚本化响应顺序）
    - 其余单测：默认关闭（保持 v2 基础链路行为，脚本化 LLM 响应不受影响）
    - integration 测试：不改，跟随 .env 默认（真实走评审回路）
    """
    if "integration" in request.keywords:
        yield
        return
    enabled = "query_review" in request.keywords
    monkeypatch.setattr(config, "ENABLE_QUERY_REVIEW", enabled)
    # 默认走 on_revise（省一次调用）；需要 write→optimize→review 顺序的测试显式标注
    strategy = "always" if "optimize_always" in request.keywords else "on_revise"
    monkeypatch.setattr(config, "OPTIMIZE_STRATEGY", strategy)
    yield
