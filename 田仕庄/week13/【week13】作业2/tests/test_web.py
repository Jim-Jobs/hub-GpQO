"""Web API 测试（server.py）：FastAPI TestClient + FakeLLM 注入，全部离线。

覆盖：健康检查、新会话、SSE 事件流协议（node/sql/result/done）、
多轮会话记忆（thread_id 复用）、空问题校验、执行失败路径（错误事件）、
静态前端托管。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fakes import FakeLLM
from langgraph.checkpoint.memory import MemorySaver

from server import _DIST_DIR, create_app
from sql_agent.db_parser import DBParser
from sql_agent.graph import build_agent

GOOD_SQL = "```sql\nSELECT Name, COUNT(*) AS n FROM tracks GROUP BY Name ORDER BY n DESC LIMIT 5\n```"
BAD_SQL = "```sql\nSELECT NotExistColumn FROM tracks LIMIT 5\n```"
ANSWER = "查询完成：共 5 条记录。"
FAIL_ANSWER_PREFIX = "很抱歉"


def parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析为 [(event, data), ...]。"""
    events: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        event, data = "message", ""
        for line in frame.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data += line[len("data:"):].strip()
        if data:
            events.append((event, json.loads(data)))
    return events


def make_client(responses: list[str], parser: DBParser) -> TestClient:
    """用 FakeLLM 构建注入式 Agent + Web 应用。"""
    llm = FakeLLM(responses)
    agent = build_agent(llm=llm, parser=parser, checkpointer=MemorySaver())
    return TestClient(create_app(agent=agent)), llm


# ---------------------------------------------------------------------------
# 基础接口
# ---------------------------------------------------------------------------
def test_health(parser):
    """健康检查返回运行配置。"""
    client, _ = make_client([], parser)
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "review" in body and "strategy" in body


def test_new_session(parser):
    """新会话接口返回合法 thread_id，且两次调用不重复。"""
    client, _ = make_client([], parser)
    ids = {client.post("/api/session/new").json()["thread_id"] for _ in range(2)}
    assert len(ids) == 2
    assert all(isinstance(t, str) and t for t in ids)


def test_chat_reject_invalid_question(parser):
    """空问题 / 超长问题应被 pydantic 校验拒绝（422）。"""
    client, _ = make_client([], parser)
    assert client.post("/api/chat", json={"question": ""}).status_code == 422
    assert client.post(
        "/api/chat", json={"question": "x" * 2001}).status_code == 422


# ---------------------------------------------------------------------------
# SSE 问答链路
# ---------------------------------------------------------------------------
def test_chat_sse_events(parser):
    """完整问答：SSE 流应按序给出 node → sql → result → done 事件。"""
    client, _ = make_client([GOOD_SQL, ANSWER], parser)
    r = client.post("/api/chat", json={"question": "歌曲最多的前5首曲目？"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(r.text)
    kinds = [e for e, _ in events]

    # 节点进度：基础链路 4 个节点（评审回路在单测默认关闭）
    nodes = [d["node"] for k, d in events if k == "node"]
    assert nodes == ["get_schema", "write_query", "execute_query",
                     "generate_answer"]
    # 节点事件带标签与耗时
    node_event = next(d for k, d in events if k == "node")
    assert node_event["label"] and node_event["elapsed"] >= 0

    # SQL 事件：write_query 产出后立即推送
    sql_event = next(d for k, d in events if k == "sql")
    assert "FROM tracks" in sql_event["sql"]

    # 结果事件：Markdown 表格
    result_event = next(d for k, d in events if k == "result")
    assert result_event["error"] == ""
    assert "|" in result_event["query_result"]

    # done 事件：完整回答 + 总耗时 + 分节点耗时
    done = dict(events[kinds.index("done")][1]) if "done" in kinds else {}
    assert done["answer"] == ANSWER
    assert done["elapsed"] >= 0
    assert done["timings"] and done["timings"][0][0] == "get_schema"
    assert "error" not in kinds  # 无异常事件


def test_chat_multi_turn_memory(parser):
    """同一 thread_id 连续两问：第二问的提示词应包含对话历史（多轮指代）。"""
    ANSWER2 = "前 10 名如下。"
    client, llm = make_client(
        [GOOD_SQL, ANSWER, GOOD_SQL, ANSWER2], parser)

    r1 = client.post("/api/chat", json={"question": "前5名曲目", "thread_id": "t1"})
    r2 = client.post("/api/chat", json={"question": "那前10名呢？", "thread_id": "t1"})
    assert r1.status_code == r2.status_code == 200

    done2 = next(d for k, d in parse_sse(r2.text) if k == "done")
    assert done2["answer"] == ANSWER2

    # 第二问的 write_query 调用应注入历史（指代解析）
    assert any("对话历史" in str(c) for c in llm.calls[2:])


def test_chat_error_path(parser):
    """SQL 连续失败重试耗尽：SSE 仍正常收尾，done 给出失败说明。"""
    # 1 次写错 + 3 次重写全错（MAX_RETRIES=3），最后 generate_answer 走失败兜底
    client, _ = make_client([BAD_SQL, BAD_SQL, BAD_SQL, BAD_SQL], parser)
    r = client.post("/api/chat", json={"question": "查一下不存在的列"})
    assert r.status_code == 200

    events = parse_sse(r.text)
    nodes = [d["node"] for k, d in events if k == "node"]
    assert nodes.count("rewrite_query") == 3  # 重试 3 次

    result_events = [d for k, d in events if k == "result"]
    assert all(d["error"] for d in result_events)  # 每次执行都报错

    done = next(d for k, d in events if k == "done")
    assert done["answer"].startswith(FAIL_ANSWER_PREFIX)
    assert done["error"]


# ---------------------------------------------------------------------------
# 极速模式 / token 批处理（性能优化项）
# ---------------------------------------------------------------------------
@pytest.mark.query_review
def test_review_loop_present_when_enabled(parser):
    """评审回路开启时，节点序列中应出现 review_query。"""
    client, _ = make_client([GOOD_SQL, '{"verdict": "approve"}', ANSWER], parser)
    nodes = [d["node"] for k, d in parse_sse(
        client.post("/api/chat", json={"question": "前5名曲目"}).text) if k == "node"]
    assert "review_query" in nodes


def test_chat_fast_mode_skips_review(parser):
    """极速模式（fast=true）：节点序列为 write → execute → answer（无评审）。"""
    client, _ = make_client([GOOD_SQL, ANSWER], parser)
    r = client.post("/api/chat", json={"question": "前5名曲目", "fast": True})
    assert r.status_code == 200
    assert [d["node"] for k, d in parse_sse(r.text) if k == "node"] == [
        "get_schema", "write_query", "execute_query", "generate_answer"]


def test_health_reports_fast_mode(parser):
    """健康检查应报告极速模式可用（真实 app 构建两条图 → True）。"""
    client, _ = make_client([], parser)
    body = client.get("/api/health").json()
    assert "fast_mode" in body  # 注入 Fake agent 时为 False，真实 app 为 True


def test_token_batcher_merges_and_keeps_text():
    """token 批处理：首块立即下发、窗口内累积、flush 后内容不丢不乱序。"""
    from server import _TokenBatcher

    out: list[str] = []
    batcher = _TokenBatcher(out.append)

    batcher.feed("你")                 # 首块：立即下发（保证首 token 延迟）
    assert out == ["你"]
    batcher.feed("好")                 # 未跨窗口 → 累积
    assert out == ["你"]
    batcher.flush()                    # worker 收尾路径
    assert out == ["你", "好"]

    long_text = "x" * (_TokenBatcher.MAX_CHARS + 1)
    batcher.feed(long_text)            # 超过字符上限 → 立即下发
    assert out[-1] == long_text
    assert "".join(out) == "你好" + long_text


def test_token_text_matches_answer(parser):
    """token 事件拼接必须等于最终回答；无流式能力时整段由 done 兜底。"""
    client, _ = make_client([GOOD_SQL, ANSWER], parser)
    events = parse_sse(client.post("/api/chat", json={"question": "前5名曲目"}).text)
    tokens = [d["text"] for k, d in events if k == "token"]
    done = next(d for k, d in events if k == "done")
    if tokens:
        assert "".join(tokens).strip() == done["answer"].strip()
    else:
        # 注入的 Fake agent 未绑定 on_token → 回退一次性生成，回答整段在 done 里
        assert done["answer"] == ANSWER


def test_warmup_tolerates_failures():
    """预热失败（schema 抛错 / LLM 无连接池）不应抛异常、不影响服务可用。"""
    from server import _warmup

    class Boom:
        table_names = ["tracks"]

        def schema_markdown(self, *a, **kw):
            raise RuntimeError("boom")

    _warmup(Boom(), [object()])  # 不抛异常即通过


# ---------------------------------------------------------------------------
# 连接健壮性：CORS 与 SSE 心跳（浏览器 "Failed to fetch" 的两个真实成因）
# ---------------------------------------------------------------------------
class _SlowAgent:
    """不跑 LLM 的假图：按指定耗时产出一次更新，用于验证心跳与总超时。"""

    def __init__(self, delay: float, node: str = "generate_answer"):
        self.delay = delay
        self.node = node

    def stream(self, state, config=None, stream_mode=None):
        time.sleep(self.delay)
        yield {self.node: {"answer": "慢速回答"}}


def test_cors_allows_cross_origin(parser):
    """跨域必须可用：缺失时浏览器拦截请求，并统一表现为 Failed to fetch。"""
    client, _ = make_client([], parser)
    pre = client.options(
        "/api/chat",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert pre.headers.get("access-control-allow-origin") == "*"

    r = client.get("/api/health", headers={"Origin": "http://localhost:5173"})
    assert r.headers.get("access-control-allow-origin") == "*"


def test_sse_heartbeat_keeps_stream_alive(parser, monkeypatch):
    """空闲期发送心跳注释帧（前端忽略），避免代理/浏览器判定连接断开。"""
    import server

    monkeypatch.setattr(server, "_HEARTBEAT_INTERVAL", 0.05)
    client = TestClient(create_app(agent=_SlowAgent(0.3)))
    text = client.post("/api/chat", json={"question": "慢问题"}).text

    assert ": ping" in text  # 心跳帧已下发
    events = parse_sse(text)
    assert [k for k, _ in events][-1] == "done"  # 心跳不影响业务事件
    assert events[-1][1]["answer"] == "慢速回答"


def test_sse_timeout_emits_error_and_releases_lock(parser, monkeypatch):
    """worker 卡死时按累计空闲超时给出 error 事件，并释放串行锁。"""
    import server

    monkeypatch.setattr(server, "_HEARTBEAT_INTERVAL", 0.02)
    monkeypatch.setattr(server, "_EVENT_TIMEOUT", 0.06)
    client = TestClient(create_app(agent=_SlowAgent(0.4)))
    text = client.post("/api/chat", json={"question": "卡住的问题"}).text

    events = parse_sse(text)
    assert [k for k, _ in events][-1] == "error"
    assert "超时" in events[-1][1]["message"]

    # 锁必须已释放：否则后续请求永远 409
    assert client.post("/api/chat", json={"question": "再来一次"}).status_code == 200


def test_answer_cache_hit_replays_without_llm(parser):
    """新会话重复问同一题：直接回放事件，不再调用 LLM。"""
    client, llm = make_client([GOOD_SQL, ANSWER], parser)
    first = client.post("/api/chat",
                        json={"question": "重复问题", "thread_id": "cache-t1"})
    assert first.status_code == 200
    events1 = parse_sse(first.text)
    assert events1[-1][0] == "done"
    n_calls = len(llm.calls)

    # 新会话（历史为空）问同一题 → 命中；FakeLLM 已被耗尽，若真跑会报错
    second = client.post("/api/chat",
                          json={"question": "重复问题", "thread_id": "cache-t2"})
    assert second.status_code == 200
    events2 = parse_sse(second.text)
    done = events2[-1][1]
    assert done.get("cached") is True
    assert done.get("origin_elapsed") == events1[-1][1]["elapsed"]
    assert done["answer"] == ANSWER
    assert not any(k == "error" for k, _ in events2)
    assert len(llm.calls) == n_calls  # 关键：没有再跑 LLM
    # 回放时节点耗时置空，避免把上次的耗时误当成本次
    assert all(d.get("elapsed") is None for k, d in events2 if k == "node")


def test_answer_cache_respects_conversation_history(parser):
    """同一会话的追问历史不同，不能误命中缓存导致串答案。"""
    client, _ = make_client([GOOD_SQL, ANSWER, GOOD_SQL, "第二轮回答"], parser)
    client.post("/api/chat", json={"question": "第一轮", "thread_id": "multi-t"})
    r2 = client.post("/api/chat", json={"question": "第一轮",
                                        "thread_id": "multi-t"})
    done = parse_sse(r2.text)[-1][1]
    assert not done.get("cached")
    assert done["answer"] == "第二轮回答"


def test_answer_cache_can_be_bypassed(parser, monkeypatch):
    """nocache 请求与 WEB_CACHE_TTL=0 都能关闭缓存（基准测试用）。"""
    from sql_agent import config
    import server

    client, llm = make_client([GOOD_SQL, ANSWER, GOOD_SQL, ANSWER], parser)
    client.post("/api/chat", json={"question": "题目", "thread_id": "t1"})
    r = client.post("/api/chat", json={"question": "题目", "thread_id": "t2",
                                       "nocache": True})
    assert not parse_sse(r.text)[-1][1].get("cached")
    assert len(llm.calls) == 4

    monkeypatch.setattr(config, "WEB_CACHE_TTL", 0)
    disabled = TestClient(create_app(agent=build_agent(
        llm=FakeLLM([GOOD_SQL, ANSWER, GOOD_SQL, ANSWER]),
        parser=parser, checkpointer=MemorySaver())))
    disabled.post("/api/chat", json={"question": "题目", "thread_id": "t1"})
    r2 = disabled.post("/api/chat", json={"question": "题目", "thread_id": "t2"})
    assert not parse_sse(r2.text)[-1][1].get("cached")


def test_health_reports_cache_state(parser):
    """健康检查暴露缓存开关与当前条目数，便于排查。"""
    client, _ = make_client([], parser)
    body = client.get("/api/health").json()
    assert body["cache"]["enabled"] is True
    assert body["cache"]["size"] == 0


def test_lock_guard_release_is_idempotent(monkeypatch):
    """worker / 生成器 / background 三处释放只生效一次，且换锁后旧 guard 不误放。"""
    import server

    lock = threading.Lock()
    monkeypatch.setattr(server, "_chat_lock", lock)
    assert lock.acquire(blocking=False)
    guard = server._LockGuard(lock)
    guard.release()
    guard.release()
    assert guard.released
    assert lock.acquire(blocking=False)  # 已释放
    lock.release()

    # 自愈换了新锁后，旧 guard 的迟到释放不能把新锁放开
    fresh = threading.Lock()
    assert fresh.acquire(blocking=False)
    monkeypatch.setattr(server, "_chat_lock", fresh)
    server._LockGuard(lock).release()
    assert fresh.locked()
    fresh.release()


def test_leaked_lock_self_heals(monkeypatch):
    """锁泄漏（响应未启动即断开 / 长时间未释放）时，下一次请求自动恢复。"""
    import server

    leaked = threading.Lock()
    leaked.acquire()  # 模拟泄漏，永不释放
    old_guard = server._LockGuard(leaked)  # started 未置位
    monkeypatch.setattr(server, "_chat_lock", leaked)
    monkeypatch.setattr(server, "_active_guard", old_guard)
    monkeypatch.setattr(server, "_lock_acquired_at", time.monotonic() - 6)

    guard = server._acquire_chat_lock()  # 未启动路径
    assert guard is not None and guard is not old_guard
    assert server._chat_lock is not leaked
    guard.release()

    # 长时间持锁路径
    leaked2 = threading.Lock()
    leaked2.acquire()
    monkeypatch.setattr(server, "_chat_lock", leaked2)
    monkeypatch.setattr(server, "_active_guard", None)
    monkeypatch.setattr(server, "_lock_acquired_at",
                        time.monotonic() - server._STALE_LOCK_TIMEOUT - 1)
    guard2 = server._acquire_chat_lock()
    assert guard2 is not None
    guard2.release()
    assert server._chat_lock.acquire(blocking=False)
    server._chat_lock.release()


def test_client_disconnect_does_not_leak_lock(parser):
    """客户端读到一半就断开：worker 收尾后锁释放，后续请求不应 409。"""
    client = TestClient(create_app(agent=_SlowAgent(0.2)))
    with client.stream("POST", "/api/chat", json={"question": "断开测试"}) as r:
        assert r.status_code == 200
        for _ in r.iter_text():
            break  # 只读一个 chunk，模拟中途关闭页面
    time.sleep(0.5)  # 等 worker 的 finally 兜底释放
    assert client.post("/api/chat", json={"question": "再来一次"}).status_code == 200


@pytest.mark.skipif(not (_DIST_DIR / "index.html").exists(),
                    reason="前端尚未构建（npm run build）")
def test_static_index_served(parser):
    """构建产物存在时，/ 应返回前端页面。"""
    client, _ = make_client([], parser)
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert b"<div id=\"app\">" in r.content or b'id="app"' in r.content


# ---------------------------------------------------------------------------
# 真实 LLM 集成（依赖 .env 有效 key；SSE 流式端到端）
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_chat_integration_real_llm():
    """真实链路：SSE 事件齐全、回答流式 token 生效、结果为 Markdown 表格。"""
    client = TestClient(create_app())  # 真实 LLM + 流式 hub + 极速模式图
    r = client.post("/api/chat", json={"question": "每种音乐流派各有多少首歌曲？"})
    assert r.status_code == 200

    events = parse_sse(r.text)
    kinds = [k for k, _ in events]

    nodes = [d["node"] for k, d in events if k == "node"]
    assert nodes[0] == "get_schema"
    assert nodes[-1] == "generate_answer"
    assert "write_query" in nodes and "execute_query" in nodes

    sql_event = next(d for k, d in events if k == "sql")
    assert sql_event["sql"].upper().startswith(("SELECT", "WITH"))

    result_event = next(d for k, d in events if k == "result")
    assert result_event["error"] == ""
    assert "|" in result_event["query_result"]

    # 流式回答：应有 token 事件，且拼接后与最终回答一致
    tokens = "".join(d["text"] for k, d in events if k == "token")
    done = next(d for k, d in events if k == "done")
    assert done["answer"]
    assert tokens.strip() == done["answer"].strip()
    assert done["elapsed"] > 0
    assert "error" not in kinds
