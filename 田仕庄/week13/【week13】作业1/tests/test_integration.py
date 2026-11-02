"""真实 LLM 集成测试（需要 .env 配置 LLM_API_KEY）。

默认被跳过，显式运行：
    pytest -m integration -v
"""

import pytest

from sql_agent import config

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_agent():
    if not config.LLM_API_KEY:
        pytest.skip("未配置 LLM_API_KEY，跳过真实 LLM 集成测试")
    from sql_agent.graph import build_agent
    return build_agent()


def test_simple_count(real_agent):
    state = real_agent.invoke({"question": "数据库里一共有多少位客户？"})
    assert state["error"] == ""
    assert "59" in state["answer"]  # chinook customers 固定 59 位


def test_top_query(real_agent):
    state = real_agent.invoke({"question": "歌曲数量最多的前3个音乐流派是什么？"})
    assert state["error"] == ""
    assert "Rock" in state["answer"]  # Rock 以 1297 首断层第一


def test_error_recovery(real_agent):
    """模糊问题也应最终给出回答（成功答案或友好的失败说明）。"""
    state = real_agent.invoke({"question": "哪个国家的客户消费总额最高？"})
    assert state.get("answer")
    assert state["error"] == ""  # 应能通过重试回路正确执行
