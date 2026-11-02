"""Agent 图流程自动化测试（mock LLM，离线可跑）。

通过 FakeLLM 脚本化 LLM 响应，验证：
1. 成功路径：一次生成正确 SQL，直达 generate_answer
2. 纠错回路：首次 SQL 错误 → rewrite → 成功
3. 重试耗尽：多次失败 → 失败兜底回答
4. 安全校验回路：LLM 生成写操作 SQL → 被拦截 → 重写
"""

import pytest

from sql_agent import config
from sql_agent.db_parser import DBParser
from sql_agent.graph import _route_after_execute, build_agent
from sql_agent.state import AgentState


class FakeResp:
    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """脚本化 LLM：按顺序弹出预设响应，并记录调用。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return FakeResp(self.responses.pop(0))


@pytest.fixture(scope="module")
def parser() -> DBParser:
    return DBParser()


GOOD_SQL = "```sql\nSELECT Name, COUNT(*) AS n FROM tracks ORDER BY n DESC LIMIT 5\n```"
BAD_SQL = "```sql\nSELECT NotExistColumn FROM tracks LIMIT 5\n```"
WRITE_SQL = "```sql\nDELETE FROM tracks\n```"


# ---------------------------------------------------------------------------
# 图结构测试
# ---------------------------------------------------------------------------
def test_graph_structure():
    """未编译的 builder 应包含 5 个节点。"""
    builder = build_agent(llm=FakeLLM(["x"]), compiled=False)
    assert set(builder.nodes) == {
        "get_schema", "write_query", "execute_query",
        "rewrite_query", "generate_answer",
    }


def test_route_after_execute():
    """条件边路由逻辑。"""
    ok = _route_after_execute({"error": ""})
    assert ok == "generate_answer"

    fail_first = _route_after_execute({"error": "boom", "retry_count": 0})
    assert fail_first == "rewrite_query"

    exhausted = _route_after_execute({"error": "boom", "retry_count": config.MAX_RETRIES})
    assert exhausted == "generate_answer"


# ---------------------------------------------------------------------------
# 全流程测试
# ---------------------------------------------------------------------------
def test_success_path(parser: DBParser):
    """一次成功：LLM 只被调用 2 次（write_query + generate_answer）。"""
    llm = FakeLLM([GOOD_SQL, "Rock 类型的歌曲最多。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "数量最多的前5首歌曲是什么类型？"})

    assert state["error"] == ""
    assert "SELECT" in state["sql"].upper()
    assert "Rock" in state["answer"]
    assert len(llm.calls) == 2  # 无重试


def test_retry_success_path(parser: DBParser):
    """首次 SQL 错误 → 携错误反馈重写 → 第二次成功。"""
    llm = FakeLLM([BAD_SQL, GOOD_SQL, "重试后的回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "查询歌曲数量"})

    assert state["error"] == ""
    assert state["retry_count"] == 1
    assert len(llm.calls) == 3  # write + rewrite + answer
    # 重写提示中应包含错误信息与上一次 SQL（纠错回路闭环）
    rewrite_prompt = llm.calls[1][1][1]  # ("human", prompt) 中的 prompt
    assert "NotExistColumn" in rewrite_prompt
    assert "错误信息" in rewrite_prompt


def test_retry_exhausted_path(parser: DBParser):
    """连续失败直到重试上限 → 失败兜底回答，不再调用 LLM 生成答案。"""
    n_fail = config.MAX_RETRIES + 1  # 首次 + MAX_RETRIES 次重写，全部失败
    llm = FakeLLM([BAD_SQL] * n_fail)
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "查询一些数据"})

    assert state["error"] != ""
    assert state["retry_count"] == config.MAX_RETRIES
    assert "未能生成" in state["answer"] or "失败" in state["answer"]
    # 失败收尾不调用 LLM：总调用数 = 1(write) + MAX_RETRIES(rewrite)
    assert len(llm.calls) == 1 + config.MAX_RETRIES


def test_write_sql_blocked_and_recovered(parser: DBParser):
    """LLM 生成写操作 SQL → 安全校验拦截 → 重写为合法只读 SQL → 成功。"""
    llm = FakeLLM([WRITE_SQL, GOOD_SQL, "已改为只读查询并回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "删除所有歌曲"})

    assert state["retry_count"] == 1
    assert state["error"] == ""
    assert "SELECT" in state["sql"].upper()


def test_schema_matching(parser: DBParser):
    """get_schema 关键词命中：中文问题应命中相关英文表。"""
    llm = FakeLLM([GOOD_SQL, "答"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "销量最高的艺术家是谁？"})

    # invoices（销量）命中后，外键关联表应被扩充进上下文
    assert "artists" in state["table_names"] or "albums" in state["table_names"]
    assert "invoices" in state["table_names"]
    assert "tracks" in state["table_names"]  # 通用兜底关键词"最高"
    # 上下文应包含外键关系说明
    assert "外键关系" in state["table_schemas"]


def test_state_contract(parser: DBParser):
    """最终 state 应满足 AgentState 契约的关键字段。"""
    llm = FakeLLM([GOOD_SQL, "回答完成。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "测试问题"})
    for key in AgentState.__annotations__:
        assert key in state, f"state 缺少字段 {key}"


def test_multi_turn_memory(parser: DBParser):
    """多轮会话记忆：checkpointer + 同一 thread_id 下，第二轮提示包含第一轮问答。"""
    from langgraph.checkpoint.memory import MemorySaver

    llm = FakeLLM([GOOD_SQL, "第一轮回答：Rock 最多。",
                   GOOD_SQL, "第二轮回答：基于前10名继续。"])
    agent = build_agent(llm=llm, parser=parser, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "t1"}}

    s1 = agent.invoke({"question": "歌曲最多的流派是什么？"}, config=cfg)
    assert s1["history"] == [("歌曲最多的流派是什么？", "第一轮回答：Rock 最多。")]

    s2 = agent.invoke({"question": "那前10名呢？"}, config=cfg)
    # 第二轮 write_query 的提示词应包含第一轮的问题与回答（指代消解依据）
    second_write_prompt = llm.calls[2][1][1]  # calls: [write1, answer1, write2, ...]
    assert "歌曲最多的流派" in second_write_prompt
    assert "Rock 最多" in second_write_prompt
    # history 随轮次累积
    assert len(s2["history"]) == 2


def test_multi_turn_isolated_threads(parser: DBParser):
    """不同 thread_id 会话隔离：互不可见对方历史。"""
    from langgraph.checkpoint.memory import MemorySaver

    llm = FakeLLM([GOOD_SQL, "A会话回答。", GOOD_SQL, "B会话回答。"])
    agent = build_agent(llm=llm, parser=parser, checkpointer=MemorySaver())

    agent.invoke({"question": "A问题"}, config={"configurable": {"thread_id": "a"}})
    s_b = agent.invoke({"question": "B问题"}, config={"configurable": {"thread_id": "b"}})

    # B 会话的 write_query 提示不应包含 A 会话内容
    b_write_prompt = llm.calls[2][1][1]
    assert "A问题" not in b_write_prompt
    assert "A会话回答" not in b_write_prompt
    assert len(s_b["history"]) == 1


# ===========================================================================
# 评审回路（Reflection 模式）：以下测试需显式开启评审（@pytest.mark.query_review）
# 流程：write_query → optimize_query → review_query →(approve)→ execute → answer
# ===========================================================================
OPT_SQL = "```sql\nSELECT Name, COUNT(*) AS n FROM tracks GROUP BY Name ORDER BY n DESC LIMIT 3\n```"
APPROVE_JSON = '{"verdict": "approve", "feedback": ""}'
REVISE_JSON = '{"verdict": "revise", "feedback": "LIMIT 应为 5 而非 3，请修正"}'


@pytest.mark.query_review
def test_review_topology(parser: DBParser):
    """开启评审后图应含 7 个节点。"""
    builder = build_agent(llm=FakeLLM(["x"]), compiled=False)
    assert set(builder.nodes) == {
        "get_schema", "write_query", "optimize_query", "review_query",
        "execute_query", "rewrite_query", "generate_answer",
    }


@pytest.mark.query_review
@pytest.mark.optimize_always
def test_review_approve_path(parser: DBParser):
    """评审一次通过：write → optimize → review(approve) → execute → answer。"""
    llm = FakeLLM([GOOD_SQL, OPT_SQL, APPROVE_JSON, "评审通过后的回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["review_verdict"] == "approve"
    assert "LIMIT 3" in state["sql"]          # 执行的是改写后的 SQL
    assert "LIMIT 5" in state["original_sql"]  # 原始 SQL 已保存
    assert len(llm.calls) == 4


@pytest.mark.query_review
@pytest.mark.optimize_always
def test_review_revise_once(parser: DBParser):
    """评审不通过 → 带意见回到改写 → 二次评审通过。"""
    llm = FakeLLM([GOOD_SQL, OPT_SQL, REVISE_JSON, GOOD_SQL, APPROVE_JSON, "回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["optimize_count"] == 2
    assert state["review_verdict"] == "approve"
    assert len(llm.calls) == 6
    # 第二次改写的提示应包含评审意见（纠错回路闭环）
    second_optimize_prompt = llm.calls[3][1][1]
    assert "LIMIT 应为 5" in second_optimize_prompt


@pytest.mark.query_review
@pytest.mark.optimize_always
def test_review_loop_limit(parser: DBParser):
    """revise 恒不通过：达到改写轮次上限后强制放行执行（不死循环）。"""
    from sql_agent import config as cfg

    llm = FakeLLM([GOOD_SQL, OPT_SQL, REVISE_JSON, OPT_SQL, REVISE_JSON, "回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["optimize_count"] == cfg.MAX_OPTIMIZE_ROUNDS
    # 调用数 = 1(write) + N(optimize) + N(review) + 1(answer)，N=改写上限
    assert len(llm.calls) == 2 * cfg.MAX_OPTIMIZE_ROUNDS + 2
    assert state["error"] == ""  # 放行的 SQL 有效，正常执行


@pytest.mark.query_review
@pytest.mark.optimize_always
def test_fallback_to_original_sql(parser: DBParser):
    """改写把好 SQL 改坏且评审未拦住 → 执行失败时自动回退原 SQL。"""
    llm = FakeLLM([GOOD_SQL, BAD_SQL, APPROVE_JSON, "回退后的回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["error"] == ""
    assert "LIMIT 5" in state["sql"]  # 最终执行的是回退后的原始 SQL
    assert len(llm.calls) == 4


# ===========================================================================
# on_revise 策略（默认）：评审驱动改写，happy path 省一次 LLM 调用
# 流程：write_query → review_query →(approve) execute → answer
#                              └─(revise)→ optimize_query → review_query
# ===========================================================================
@pytest.mark.query_review
def test_on_revise_approve_path(parser: DBParser):
    """评审直接通过：不触发改写，共 3 次 LLM 调用（always 策略需 4 次）。"""
    llm = FakeLLM([GOOD_SQL, APPROVE_JSON, "回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["review_verdict"] == "approve"
    assert state["optimize_count"] == 0            # 没有浪费一次改写调用
    assert state["sql"] == state["original_sql"]   # 执行的就是初稿 SQL
    assert len(llm.calls) == 3                     # write + review + answer


@pytest.mark.query_review
def test_on_revise_revise_path(parser: DBParser):
    """评审不通过 → 才改写 → 二次评审通过（改写提示需含评审意见）。"""
    llm = FakeLLM([GOOD_SQL, REVISE_JSON, OPT_SQL, APPROVE_JSON, "回答。"])
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["optimize_count"] == 1
    assert state["review_verdict"] == "approve"
    assert "LIMIT 3" in state["sql"]               # 执行改写后的 SQL
    assert len(llm.calls) == 5                     # write+review+optimize+review+answer
    assert "LIMIT 应为 5" in llm.calls[2][1][1]    # 改写提示携带评审意见


@pytest.mark.query_review
def test_on_revise_loop_limit(parser: DBParser):
    """revise 恒不通过：改写轮次受 MAX_OPTIMIZE_ROUNDS 限制，不死循环。"""
    from sql_agent import config as cfg

    responses = [GOOD_SQL, REVISE_JSON]
    for _ in range(cfg.MAX_OPTIMIZE_ROUNDS):
        responses += [OPT_SQL, REVISE_JSON]
    responses.append("回答。")
    llm = FakeLLM(responses)
    agent = build_agent(llm=llm, parser=parser)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["optimize_count"] == cfg.MAX_OPTIMIZE_ROUNDS
    # write 1 + review(MAX+1) + optimize(MAX) + answer 1
    assert len(llm.calls) == 2 * cfg.MAX_OPTIMIZE_ROUNDS + 3
    assert state["error"] == ""


# ===========================================================================
# 流式回答
# ===========================================================================
class FakeStreamLLM(FakeLLM):
    """带 stream() 的 Fake LLM，用于验证流式回答链路。"""

    def stream(self, messages, **kwargs):
        self.calls.append(messages)
        for ch in self.responses.pop(0):
            yield FakeResp(ch)


def test_streaming_answer(parser: DBParser):
    """on_token 回调应逐字符收到回答，且与最终 answer 一致。"""
    llm = FakeStreamLLM([GOOD_SQL, "流式回答文本"])
    tokens: list[str] = []
    agent = build_agent(llm=llm, parser=parser, on_token=tokens.append)
    state = agent.invoke({"question": "歌曲名统计"})

    assert "".join(tokens) == state["answer"] == "流式回答文本"


def test_streaming_fallback_without_stream(parser: DBParser):
    """LLM 无 stream 能力时，传了 on_token 也应正常回退为一次性返回。"""
    llm = FakeLLM([GOOD_SQL, "普通回答"])
    tokens: list[str] = []
    agent = build_agent(llm=llm, parser=parser, on_token=tokens.append)
    state = agent.invoke({"question": "歌曲名统计"})

    assert state["answer"] == "普通回答"
    assert tokens == []  # 未流式输出，答案由 CLI 整段打印
