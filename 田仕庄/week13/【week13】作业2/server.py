"""SQL Agent Web 服务入口（FastAPI + SSE 流式）。

用法：
    python server.py                     # 启动 Web 服务（默认 http://127.0.0.1:8000）
    uvicorn server:app --port 8000       # 等价方式

接口：
    GET  /api/health          健康检查 + 运行配置
    POST /api/session/new     生成新会话 thread_id（开启新对话）
    POST /api/chat            SSE 流式问答（事件：node / sql / result / token / done / error）
    GET  /                    托管前端构建产物（web/frontend/dist）

前端：Vue 3（web/frontend）。开发调试：
    python server.py                     # 后端
    cd web/frontend && npm run dev       # Vite 开发服务器，/api 自动代理到后端

设计要点（对齐 AGENTS.md）：
- Agent 依赖注入：create_app(agent=...)，测试可换 FakeLLM 构建的图
- 流式：generate_answer 的 on_token 经 thread-local 转发到当前请求的 SSE 队列，
  agent.stream 在独立 worker 线程执行，不阻塞事件循环
- 单请求串行：同一进程同一时刻只跑一个问答（LLM 是瓶颈，串行即够用；忙时返回 409）
- 日志：复用 sql_agent.log 双通道（console + 滚动文件），命名空间 sql_agent.web
"""

from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field

from sql_agent import config
from sql_agent.graph import build_agent
from sql_agent.labels import NODE_LABELS
from sql_agent.log import get_logger, setup_logging

logger = get_logger("web")

#: 前端构建产物目录（存在时由本服务静态托管）
_DIST_DIR = Path(__file__).resolve().parent / "web" / "frontend" / "dist"

#: 单条 SSE 事件等待上限（秒）：防止 worker 异常卡死导致连接悬挂
_EVENT_TIMEOUT = 300.0

#: SSE 心跳间隔（秒）：空闲期发注释帧保活，避免代理/浏览器因无数据判定连接断开
_HEARTBEAT_INTERVAL = 15.0

# ---------------------------------------------------------------------------
# 串行锁：同一时刻只允许一个问答在执行（忙时 409）。
#
# 关键坑：客户端中途断开时，Starlette 在线程池里迭代同步生成器，取消信号
# 无法传导到阻塞在 queue.get 的线程，生成器的 finally 可能永不执行 —— 锁会
# 永久泄漏，之后所有请求都返回 409（页面表现为"一直查不了"）。因此释放点
# 用幂等 guard 冗余三处：worker finally（必执行）、生成器 finally、响应
# background；再加超时自愈，整把锁可替换，旧 guard 释放时自动失效。
# ---------------------------------------------------------------------------
_chat_lock = threading.Lock()
_lock_meta = threading.Lock()
_lock_acquired_at = 0.0
_active_guard = None  # type: Optional[_LockGuard]
_STALE_LOCK_TIMEOUT = _EVENT_TIMEOUT + 60.0
#: 响应体迟迟未开始迭代（客户端在首个 chunk 前就断开）视为锁泄漏
_UNSTARTED_LOCK_TIMEOUT = 5.0

#: SSE 通用响应头（禁用缓存/代理缓冲，保持长连接）
_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


class _LockGuard:
    """幂等锁释放器：多处释放点只生效一次，且只释放「当前那把锁」。"""

    def __init__(self, lock: threading.Lock) -> None:
        self._lock = lock
        self._released = threading.Event()
        self.started = threading.Event()

    def mark_started(self) -> None:
        self.started.set()

    def release(self) -> None:
        if self._released.is_set():
            return
        self._released.set()
        # 锁若已被超时自愈整体替换，旧 guard 不再释放新锁（否则会误放给新请求）
        if _chat_lock is self._lock:
            try:
                self._lock.release()
            except RuntimeError:  # pragma: no cover - 防御：本就未锁定
                pass

    @property
    def released(self) -> bool:
        return self._released.is_set()


def _acquire_chat_lock() -> Optional[_LockGuard]:
    """获取串行锁；忙且未达自愈阈值时返回 None（由路由回 409）。"""
    global _chat_lock, _lock_acquired_at, _active_guard

    lock = _chat_lock
    if lock.acquire(blocking=False):
        guard = _LockGuard(lock)
        with _lock_meta:
            _lock_acquired_at = time.monotonic()
            _active_guard = guard
        return guard

    with _lock_meta:
        elapsed = time.monotonic() - _lock_acquired_at
        current = _active_guard
    never_started = (current is not None and not current.started.is_set()
                     and elapsed > _UNSTARTED_LOCK_TIMEOUT)
    stale = elapsed > _STALE_LOCK_TIMEOUT or never_started
    if not stale:
        return None

    # 自愈：换一把新锁，旧 guard 的 release 因锁实例变更自动失效
    logger.warning("chat: 串行锁未释放（%.1fs, 响应%s），已自动恢复",
                   elapsed, "未启动" if never_started else "超时")
    fresh = threading.Lock()
    new_guard = _LockGuard(fresh)
    with _lock_meta:
        _chat_lock = fresh
        _lock_acquired_at = time.monotonic()
        _active_guard = new_guard
    assert fresh.acquire(blocking=False)
    return new_guard


# ---------------------------------------------------------------------------
# 流式 token 转发：agent.stream 在 worker 线程执行，on_token 回调发生在同一
# worker 线程内，因此用 thread-local 绑定当前请求的队列即可做到并发安全。
# ---------------------------------------------------------------------------
class _StreamHub:
    """thread-local token 分发器：build_agent(on_token=hub) 时注入。"""

    def __init__(self) -> None:
        self._local = threading.local()

    def bind(self, handler: Callable[[str], None]) -> None:
        """在当前 worker 线程绑定 token 处理器（SSE 队列）。"""
        self._local.handler = handler

    def unbind(self) -> None:
        self._local.handler = None

    def __call__(self, token: str) -> None:
        handler = getattr(self._local, "handler", None)
        if handler is not None:
            handler(token)


# ---------------------------------------------------------------------------
# 问答缓存：Chinook 是静态只读库，同一问题在同一会话上下文下结果恒定。
# 缓存完整 SSE 事件序列，命中时直接回放，跳过两次 LLM 往返（TTFT → ~0.01s）。
# ---------------------------------------------------------------------------
class _AnswerCache:
    """线程安全的 TTL + LRU 缓存，value 为 [(event, payload), ...]。"""

    def __init__(self, ttl: float, max_size: int) -> None:
        self.enabled = ttl > 0 and max_size > 0
        self.ttl = ttl
        self.max_size = max_size
        self._data: OrderedDict[str, tuple[float, list[tuple[str, dict]]]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[list[tuple[str, dict]]]:
        if not self.enabled:
            return None
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            ts, events = item
            if time.monotonic() - ts > self.ttl:
                del self._data[key]
                return None
            self._data.move_to_end(key)
            return events

    def put(self, key: str, events: list[tuple[str, dict]]) -> None:
        if not self.enabled:
            return
        with self._lock:
            self._data[key] = (time.monotonic(), events)
            self._data.move_to_end(key)
            while len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)


def _cache_key(agent, question: str, thread_id: str, fast: bool) -> str:
    """缓存键 = 问题 + 模式 + 会话历史（多轮指代不会串答案）。"""
    try:
        state = agent.get_state({"configurable": {"thread_id": thread_id}})
        history = list((state.values or {}).get("history") or [])
    except Exception:  # noqa: BLE001 假图/无 checkpointer 时退化为无历史
        history = []
    raw = json.dumps([question, fast, [str(h) for h in history]],
                     ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _replay_stream(events: list[tuple[str, dict]], thread_id: str):
    """缓存命中：回放事件序列。节点耗时置空、done 标记 cached。"""
    for kind, payload in events:
        if kind == "node":
            payload = {**payload, "elapsed": None}
        elif kind == "done":
            payload = {**payload, "cached": True,
                       "origin_elapsed": payload.get("elapsed"),
                       "elapsed": 0.01}
        yield (f"event: {kind}\n"
               f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    logger.debug("chat cache replay: thread=%s events=%d", thread_id, len(events))


# ---------------------------------------------------------------------------
# token 批量下发：LLM 流式常是"逐字"的（一次回答可产生上百个事件），
# 逐字下发会让前端每个字都触发一次响应式更新 + 滚动。按时间窗口合并后，
# 事件数降一个数量级，且最多只增加 ~40ms 延迟（首块立即发，保证 TTFT 不退化）。
# ---------------------------------------------------------------------------
class _TokenBatcher:
    """合并细碎 token：首块立即下发，之后按窗口批量下发。"""

    #: 合并窗口（秒）；超过窗口或累积字符数即下发
    WINDOW = 0.04
    MAX_CHARS = 64

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit
        self._buf: list[str] = []
        self._size = 0
        self._last = 0.0

    def feed(self, token: str) -> None:
        self._buf.append(token)
        self._size += len(token)
        now = time.perf_counter()
        # 首块立即发（保证首 token 延迟不受影响）
        if self._last == 0.0 or now - self._last >= self.WINDOW \
                or self._size >= self.MAX_CHARS:
            self.flush(now)

    def flush(self, now: float | None = None) -> None:
        if self._buf:
            self._emit("".join(self._buf))
            self._buf.clear()
            self._size = 0
        self._last = now if now is not None else time.perf_counter()


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000, description="用户自然语言问题")
    thread_id: str = Field(default="", max_length=64, description="会话 ID，空则服务端生成")
    fast: bool = Field(default=False, description="极速模式：跳过评审回路（延迟更低）")
    nocache: bool = Field(default=False, description="跳过问答缓存（性能基准用）")


def _build_agent_with_token(main_llm, aux_llm, parser, on_token, review: bool):
    """按评审开关构建图（拓扑在编译期决定，故极速/严谨各建一条）。

    运行时读取的 config（MAX_RETRIES 等）不受影响：此处只在编译期临时改写
    ENABLE_QUERY_REVIEW，构建完立即还原。
    """
    origin = config.ENABLE_QUERY_REVIEW
    config.ENABLE_QUERY_REVIEW = review
    try:
        return build_agent(
            llm=main_llm,
            fast_llm=aux_llm,
            parser=parser,
            checkpointer=MemorySaver(),  # 内存级会话记忆，支持多轮指代
            on_token=on_token,
        )
    finally:
        config.ENABLE_QUERY_REVIEW = origin


def _warmup(parser, llms) -> None:
    """启动预热：把首次请求的前置开销（schema 缓存、TLS 握手）挪到启动阶段。"""
    try:
        # 1) schema 片段缓存（含"命中关键词"与"兜底全表"两种形态）
        tables = list(parser.table_names)
        for rows in (config.SCHEMA_SAMPLE_ROWS, 0):
            parser.schema_markdown(tables, sample_rows=rows)
    except Exception as e:  # noqa: BLE001 预热失败不影响服务可用
        logger.warning("warmup: schema 预热失败 - %s", e)

    # 2) LLM 连接池：发一次轻量请求完成 TCP/TLS 握手（不带 body，不消耗 token）
    for llm in llms:
        client = getattr(llm, "http", None)
        if client is None:
            continue
        try:
            client.get("", timeout=5)
        except Exception:  # noqa: BLE001 405/404 属正常，连接已建立
            pass
    logger.info("warmup: 预热完成")


# ---------------------------------------------------------------------------
# 应用工厂
# ---------------------------------------------------------------------------
def create_app(agent=None, static_dir: Optional[Path] = None) -> FastAPI:
    """构建 FastAPI 应用。

    参数：
        agent:      编译后的 LangGraph 图（build_agent() 产物）；缺省在进程内
                    构建真实 LLM + MemorySaver 的默认 Agent。测试注入 FakeLLM 图。
        static_dir: 前端静态目录，缺省 web/frontend/dist（不存在则不托管）。
    """
    static_dir = Path(static_dir) if static_dir else _DIST_DIR

    fast_agent = None
    if agent is None:
        from sql_agent.db_parser import DBParser
        from sql_agent.llm import get_fast_llm, get_llm

        hub = _StreamHub()
        parser = DBParser()  # 与 Agent 共用实例，便于预热 schema 缓存
        main_llm, aux_llm = get_llm(), get_fast_llm()
        on_token = hub if config.STREAM_ANSWER else None
        # 注：build_agent 内 on_token 固定给 generate_answer，两条图共用同一 hub
        hub_stream = on_token
        agent = _build_agent_with_token(main_llm, aux_llm, parser,
                                        hub_stream, config.ENABLE_QUERY_REVIEW)
        # 极速模式：跳过评审回路（write → execute → answer），TTFT 更低
        fast_agent = _build_agent_with_token(
            main_llm, aux_llm, parser, hub_stream, review=False)
        streaming = config.STREAM_ANSWER
        if config.WEB_WARMUP:
            threading.Thread(
                target=_warmup, args=(parser, [main_llm, aux_llm]),
                daemon=True, name="warmup",
            ).start()
    else:
        hub = None
        streaming = False

    app = FastAPI(title="SQL Agent Web", version="1.0.0")

    # CORS：允许前后端分离访问（Vite dev 直连、预览面板 iframe、局域网访问）。
    # 缺失时被浏览器拦截的请求一律表现为 TypeError: Failed to fetch，无法定位，
    # 故显式放开；本服务为本地只读查询，无凭证与敏感写操作。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.agent = agent
    app.state.fast_agent = fast_agent
    app.state.hub = hub
    app.state.streaming = streaming
    app.state.cache = _AnswerCache(config.WEB_CACHE_TTL, config.WEB_CACHE_SIZE)

    # ------------------------- 健康检查 -------------------------
    @app.get("/api/health")
    def health() -> dict:
        return {
            "status": "ok",
            "review": config.ENABLE_QUERY_REVIEW,
            "strategy": config.OPTIMIZE_STRATEGY,
            "streaming": streaming,
            "fast_mode": fast_agent is not None,
            "static_served": (static_dir / "index.html").exists(),
            "cache": {"enabled": app.state.cache.enabled, "size": len(app.state.cache)},
        }

    # ------------------------- 新会话 -------------------------
    @app.post("/api/session/new")
    def new_session() -> dict:
        """生成新的 thread_id（前端切换后即开启全新对话记忆）。"""
        return {"thread_id": uuid.uuid4().hex}

    # ------------------------- 流式问答 -------------------------
    @app.post("/api/chat")
    def chat(req: ChatRequest):
        thread_id = req.thread_id.strip() or uuid.uuid4().hex
        question = req.question.strip()
        active = (app.state.fast_agent or agent) if req.fast else agent
        cache = app.state.cache
        key = None if req.nocache else _cache_key(active, question, thread_id, req.fast)

        # 命中缓存：不占串行锁、不跑 LLM，直接回放 SSE 事件（TTFT ~0.01s）
        hit = cache.get(key) if key is not None else None
        if hit is not None:
            logger.info("chat cache hit: question=%r thread=%s", question[:100], thread_id)
            return StreamingResponse(
                _replay_stream(hit, thread_id),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
            )

        guard = _acquire_chat_lock()
        if guard is None:
            logger.warning("chat: 已有请求进行中，拒绝并发 (409)")
            raise HTTPException(status_code=409, detail="已有请求正在处理，请稍候再试")

        if req.fast:
            logger.info("chat(fast): question=%r thread=%s", question[:100], thread_id)
        else:
            logger.info("chat: question=%r thread=%s", question[:100], thread_id)

        try:
            return StreamingResponse(
                _chat_event_stream(active, question, thread_id, hub, guard, cache, key),
                media_type="text/event-stream",
                headers=_SSE_HEADERS,
                background=BackgroundTask(guard.release),
            )
        except Exception:
            guard.release()
            raise

    # ------------------------- 静态前端 -------------------------
    if (static_dir / "index.html").exists():
        assets = static_dir / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(static_dir / "index.html")

        logger.debug("create_app: 静态前端已挂载 (%s)", static_dir)

    logger.info("create_app: Web 服务就绪 (streaming=%s)", streaming)
    return app


def _chat_event_stream(agent, question: str, thread_id: str, hub,
                       guard: _LockGuard, cache: _AnswerCache,
                       cache_key: Optional[str]):
    """SSE 事件生成器：worker 线程跑 agent.stream，事件经队列转发给客户端。

    事件协议（data 均为 JSON）：
        node   {node, label, elapsed}      节点完成（含耗时）
        sql    {sql}                        SQL 更新（write/optimize/rewrite 产出）
        result {query_result, error}        执行结果（Markdown 表格）或错误
        token  {text}                       回答流式 token
        done   {answer, elapsed, timings}   结束（含完整回答与分节点耗时）
        error  {message}                    异常终止
    """
    q: queue.Queue = queue.Queue()
    sentinel = object()
    record: list[tuple[str, dict]] = []  # 成功时整段写入缓存

    def emit(kind: str, payload: dict) -> None:
        record.append((kind, payload))
        q.put((kind, payload))

    def worker() -> None:
        timings: list[tuple[str, float]] = []
        final: dict = {}
        # token 批量下发：减少 SSE 事件数与前端渲染次数（首块仍立即发）
        batcher = _TokenBatcher(lambda text: emit("token", {"text": text}))
        try:
            if hub is not None:
                hub.bind(batcher.feed)
            t_start = time.perf_counter()
            t_prev = t_start
            for update in agent.stream(
                {"question": question},
                config={"configurable": {"thread_id": thread_id}},
                stream_mode="updates",
            ):
                now = time.perf_counter()
                for node, partial in (update or {}).items():
                    if partial is None:
                        continue
                    if node in NODE_LABELS:
                        dt = now - t_prev
                        timings.append((node, dt))
                        emit("node", {"node": node, "label": NODE_LABELS[node],
                                      "elapsed": round(dt, 2)})
                    final.update(partial)
                    # SQL 产出即推送（先于执行结果，保持阅读顺序）
                    if node in ("write_query", "optimize_query", "rewrite_query") \
                            and partial.get("sql"):
                        emit("sql", {"sql": partial["sql"]})
                    if node == "execute_query":
                        emit("result", {"query_result": final.get("query_result", ""),
                                        "error": final.get("error", "")})
                t_prev = now
            total = time.perf_counter() - t_start
            batcher.flush()  # 收尾：下发剩余 token（必须在 done 之前）
            logger.info("chat done: %.2fs thread=%s 节点=%s", total, thread_id,
                        ",".join(f"{n}={d:.2f}s" for n, d in timings))
            emit("done", {
                "answer": final.get("answer", ""),
                "elapsed": round(total, 2),
                "timings": [[n, round(d, 2)] for n, d in timings],
                "error": final.get("error", ""),
            })
            # 只缓存成功且有回答的链路；失败/空回答下次必须重跑
            if cache_key is not None and not final.get("error") and final.get("answer"):
                cache.put(cache_key, list(record))
        except Exception as e:  # noqa: BLE001
            logger.exception("chat worker: 异常 - %s", e)
            q.put(("error", {"message": f"处理出错: {e}"}))
        finally:
            if hub is not None:
                hub.unbind()
            q.put(sentinel)
            # 关键兜底：客户端断开时生成器 finally 可能永不执行（见锁说明），
            # worker 一定会走到这里，必须由它释放锁，否则后续请求永远 409。
            guard.release()

    def event_gen():
        idle = 0.0  # 累计空闲时长（用于总超时判定，心跳不重置）
        guard.mark_started()  # 响应体已开始迭代，排除"未启动即断开"的误判
        try:
            threading.Thread(target=worker, daemon=True,
                             name=f"chat-{thread_id}").start()
            while True:
                try:
                    item = q.get(timeout=_HEARTBEAT_INTERVAL)
                except queue.Empty:
                    idle += _HEARTBEAT_INTERVAL
                    if idle >= _EVENT_TIMEOUT:  # pragma: no cover - 防御性超时
                        yield ('event: error\ndata: '
                               + json.dumps({"message": "服务端响应超时"},
                                            ensure_ascii=False) + '\n\n')
                        break
                    # 心跳：SSE 注释帧，前端解析时忽略，仅用于保活
                    yield ": ping\n\n"
                    continue
                idle = 0.0
                if item is sentinel:
                    break
                kind, payload = item
                yield (f"event: {kind}\n"
                       f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
        finally:
            guard.release()
            logger.debug("chat stream closed: thread=%s", thread_id)

    return event_gen()


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------
def main() -> None:
    """命令行入口：python server.py"""
    import uvicorn

    setup_logging()
    try:
        app = create_app()
    except Exception as e:  # noqa: BLE001 配置缺失/数据库不可用时给出可操作提示
        logger.exception("启动失败：%s", e)
        raise SystemExit(
            f"后端启动失败：{e}\n"
            f"请检查 .env 配置（参考 .env.example），重点确认 LLM_API_KEY 与 DATABASE_URL。"
        ) from e

    logger.info("启动 Web 服务: http://%s:%s", config.WEB_HOST, config.WEB_PORT)
    try:
        uvicorn.run(app, host=config.WEB_HOST, port=config.WEB_PORT, log_level="warning")
    except OSError as e:  # 端口占用 / 地址不可用
        raise SystemExit(
            f"无法监听 {config.WEB_HOST}:{config.WEB_PORT}（{e}）。\n"
            f"若端口被占用，先停止旧进程，或在 .env 中设置 WEB_PORT 换用其他端口。"
        ) from e


if __name__ == "__main__":
    main()
