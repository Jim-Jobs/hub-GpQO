"""多轮会话状态服务：会话/轮次状态落库（chat_session / chat_turn / chat_clarify）。

职责边界
--------
- 只负责**状态管理**（存什么、留多久、怎么淘汰），不做语义改写——改写逻辑在 ``context.py``。
- ``Turn`` 保存每轮的原始问法、改写结果、DSL 与结果样本，供下一轮做指代消解与审计。
- ``SessionStore`` 以 SQLite 为后端：读取时按会话一次装配成 ``Session``/``Turn`` 视图，
  写入时逐轮回写；过期由 ``expires_at`` 滑动续期控制，容量上限按 ``updated_at`` 淘汰最旧。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from . import config
from .db.base import Base
from .db.engine import engine_for
from .db.enums import ClarifyReason, ClarifyStatus, RewriteMode, TurnStatus
from .db.models.chat import ChatClarify, ChatSession, ChatTurn

# 结果行只为「指代消解」保留少量样本（如"这些国家"引用上一轮列出的取值）
MAX_ROWS_KEPT = 100
# 拼给 LLM 的历史轮数上限
HISTORY_TURNS = 5


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Turn:
    """一轮问答的完整状态。"""

    index: int
    question: str                 # 用户原始问法
    status: str                   # ok / clarify / invalid / denied / error
    dsl: dict | None = None       # 本轮实际执行的 DSL（改写后）
    mode: str = "new"             # 上下文改写模式，见 context.rewrite
    context_note: str = ""        # 人类可读的上下文说明
    columns: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    summary: str = ""
    message: str = ""
    options: list = field(default_factory=list)
    trace: dict = field(default_factory=dict)
    sql: str = ""
    row_count: int = 0
    elapsed_ms: int | None = None
    created_at: str = field(default_factory=_now)

    def to_history_message(self) -> dict:
        """转成喂给 LLM 的一条对话记录（用户问 + 助手简述）。"""
        answer = self.summary or self.message or self.status
        dsl_part = ""
        if self.dsl:
            dims = self.dsl.get("dimensions") or []
            dsl_part = f"［指标={self.dsl.get('metric')} 维度={dims or '无'}］"
        return {"role": "assistant", "content": f"{dsl_part}{answer}"}

    def group_values(self, dim: str) -> list:
        """取本结果集中某维度的去重取值（供指代消解）。"""
        if not self.columns or dim not in self.columns:
            return []
        idx = self.columns.index(dim)
        vals = []
        for r in self.rows:
            v = r[idx] if idx < len(r) else None
            if v is None or v == "":
                continue
            if v not in vals:
                vals.append(v)
        return vals


@dataclass
class Session:
    """一次会话（同一用户的多轮对话），由存储层按需装配。"""

    session_id: str
    user_id: str
    turns: list = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    _store: object | None = field(default=None, repr=False, compare=False)

    # ---- 状态查询 ----

    def last(self) -> Turn | None:
        return self.turns[-1] if self.turns else None

    def last_ok_turn(self) -> Turn | None:
        """最近一次成功出数的轮次（指代消解的数据来源）。"""
        for t in reversed(self.turns):
            if t.status == "ok" and t.columns:
                return t
        return None

    def context_dsl(self) -> dict | None:
        """上下文基线 DSL：最近一次成功出数的轮次所用 DSL。"""
        t = self.last_ok_turn()
        return dict(t.dsl) if t and t.dsl else None

    def pending_clarify(self) -> dict | None:
        """若最后一轮是澄清且尚未被接续，返回该轮的状态（含 options 与 partial trace）。"""
        t = self.last()
        if t and t.status == "clarify":
            return {"turn": t.index, "question": t.question,
                    "message": t.message, "options": list(t.options),
                    "partial": dict(t.trace or {})}
        return None

    def history(self, n: int = HISTORY_TURNS) -> list:
        """最近 n 轮拼成对话历史（user/assistant 交替），供 LLM 解析参考。"""
        msgs = []
        for t in self.turns[-n:]:
            msgs.append({"role": "user", "content": t.question})
            msgs.append(t.to_history_message())
        return msgs

    def append(self, turn: Turn):
        """追加一轮并立即落库。"""
        if self._store is not None:
            self._store.append(self, turn)
        self.turns.append(turn)
        self.updated_at = turn.created_at

    def describe(self) -> str:
        return (f"会话 {self.session_id[:8]}… user={self.user_id} "
                f"轮次={len(self.turns)} 更新于 {self.updated_at}")


class SessionStore:
    """会话存储（SQLite）：TTL 滑动续期 + 容量上限淘汰。"""

    def __init__(self, db_path=None, ttl_s: int = 1800, max_sessions: int = 500):
        self.db_path = Path(db_path or config.META_DB)
        self.ttl_s = ttl_s
        self.max_sessions = max_sessions
        self._engine = engine_for(self.db_path)
        Base.metadata.create_all(self._engine)
        self._sf = sessionmaker(self._engine, expire_on_commit=False)
        self._hit = 0
        self._miss = 0

    # ---- 生命周期 ----

    def create(self, user_id: str = "alice") -> Session:
        self._evict()
        now = datetime.now()
        sid = uuid.uuid4().hex
        with self._sf() as db:
            db.add(ChatSession(
                session_id=sid, user_id=user_id,
                updated_at=now, expires_at=now + timedelta(seconds=self.ttl_s),
            ))
            db.commit()
        return Session(session_id=sid, user_id=user_id, _store=self)

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        now = datetime.now()
        with self._sf() as db:
            row = db.get(ChatSession, session_id)
            if row is None or row.status != "active" or row.expires_at < now:
                if row is not None and row.status == "active":
                    row.status = "expired"
                    db.commit()
                self._miss += 1
                return None
            row.updated_at = now                              # 访问即续期
            row.expires_at = now + timedelta(seconds=self.ttl_s)
            db.commit()
            turns = db.scalars(
                select(ChatTurn)
                .where(ChatTurn.session_id == session_id)
                .order_by(ChatTurn.turn_index)
            ).all()
            return Session(
                session_id=row.session_id, user_id=row.user_id,
                turns=[self._to_turn(t) for t in turns],
                created_at=row.created_at.isoformat(timespec="seconds"),
                updated_at=row.updated_at.isoformat(timespec="seconds"),
                _store=self,
            )

    def get_or_create(self, session_id: str | None, user_id: str = "alice") -> Session:
        """有则复用（续期），无则新建；传入的 session_id 若已失效会新建一个会话。"""
        s = self.get(session_id)
        if s is not None:
            self._hit += 1
            return s
        return self.create(user_id)

    def append(self, session: Session, turn: Turn):
        now = datetime.now()
        with self._sf() as db:
            sess = db.get(ChatSession, session.session_id)
            if sess is None:
                return
            db.add(ChatTurn(
                session_id=session.session_id, turn_index=turn.index,
                question=turn.question, status=TurnStatus(turn.status),
                rewrite_mode=RewriteMode(turn.mode), context_note=turn.context_note,
                dsl=turn.dsl, sql_text=turn.sql or None,
                columns=turn.columns or None,
                row_sample=turn.rows[:MAX_ROWS_KEPT] or None,
                row_count=turn.row_count or len(turn.rows),
                summary=turn.summary, message=turn.message,
                options=turn.options or None, trace=turn.trace or None,
                elapsed_ms=turn.elapsed_ms,
            ))
            if turn.status == "clarify":
                db.add(ChatClarify(
                    clarify_id=uuid.uuid4().hex, session_id=session.session_id,
                    turn_index=turn.index,
                    reason=self._clarify_reason(turn.trace, turn.message),
                    question=turn.question, partial_trace=turn.trace or None,
                    options=turn.options, status=ClarifyStatus.pending,
                    expires_at=now + timedelta(seconds=self.ttl_s),
                ))
            elif turn.mode == "clarify_pick":
                for c in db.scalars(
                    select(ChatClarify).where(
                        ChatClarify.session_id == session.session_id,
                        ChatClarify.status == ClarifyStatus.pending,
                    )
                ):
                    c.status = ClarifyStatus.resolved
                    c.resolved_choice = {"question": turn.question}
            sess.last_turn_index = max(sess.last_turn_index, turn.index)
            sess.updated_at = now
            sess.expires_at = now + timedelta(seconds=self.ttl_s)
            db.commit()

    def drop(self, session_id: str) -> bool:
        with self._sf() as db:
            row = db.get(ChatSession, session_id)
            if row is None:
                return False
            db.delete(row)          # 级联删除 turn / clarify
            db.commit()
            return True

    def clear(self):
        with self._sf() as db:
            db.execute(delete(ChatClarify))
            db.execute(delete(ChatTurn))
            db.execute(delete(ChatSession))
            db.commit()

    # ---- 维护 ----

    def _evict(self):
        """清理过期会话；若仍超容量，按 updated_at 淘汰最旧。"""
        now = datetime.now()
        with self._sf() as db:
            for row in db.scalars(
                select(ChatSession).where(
                    ChatSession.expires_at < now, ChatSession.status == "active")
            ):
                row.status = "expired"
            active = db.scalar(
                select(func.count()).select_from(ChatSession)
                .where(ChatSession.status == "active")
            )
            overflow = int(active or 0) - self.max_sessions
            if overflow >= 0:
                for row in db.scalars(
                    select(ChatSession).where(ChatSession.status == "active")
                    .order_by(ChatSession.updated_at)
                    .limit(overflow + 1)
                ):
                    row.status = "expired"
            db.commit()

    def stats(self) -> dict:
        with self._sf() as db:
            active = db.scalar(
                select(func.count()).select_from(ChatSession)
                .where(ChatSession.status == "active")
            )
            turns = db.scalar(select(func.count()).select_from(ChatTurn))
            clarifies = db.scalar(
                select(func.count()).select_from(ChatClarify)
                .where(ChatClarify.status == ClarifyStatus.pending)
            )
        return {"sessions": int(active or 0), "ttl_s": self.ttl_s,
                "max_sessions": self.max_sessions,
                "turns": int(turns or 0), "pending_clarify": int(clarifies or 0),
                "hit": self._hit, "miss": self._miss}

    # ---- 行转换 ----

    @staticmethod
    def _to_turn(t: ChatTurn) -> Turn:
        return Turn(
            index=t.turn_index, question=t.question, status=t.status.value,
            dsl=t.dsl, mode=t.rewrite_mode.value, context_note=t.context_note or "",
            columns=list(t.columns or []), rows=[list(r) for r in (t.row_sample or [])],
            summary=t.summary or "", message=t.message or "",
            options=list(t.options or []), trace=dict(t.trace or {}),
            sql=t.sql_text or "", row_count=t.row_count or 0,
            elapsed_ms=t.elapsed_ms,
            created_at=t.created_at.isoformat(timespec="seconds"),
        )

    @staticmethod
    def _clarify_reason(trace: dict, message: str) -> ClarifyReason:
        if len((trace or {}).get("metric_candidates") or []) > 1:
            return ClarifyReason.ambiguous_metric
        if "不支持" in (message or ""):
            return ClarifyReason.dim_unsupported
        return ClarifyReason.no_metric
