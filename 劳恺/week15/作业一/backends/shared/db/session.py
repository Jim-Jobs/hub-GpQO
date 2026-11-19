"""数据库访问层。

每个编排器进程持有一个 :class:`Database` 实例，指向本框架专属的 SQLite 文件。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from backends.shared.db.models import Base, Conversation, Message, utcnow

DEFAULT_TITLE = "新对话"
MAX_TITLE_LENGTH = 30


def new_conversation_id() -> str:
    return uuid.uuid4().hex


def make_title(message: str) -> str:
    """用首条用户消息生成会话标题。"""
    cleaned = " ".join(message.split())
    if not cleaned:
        return DEFAULT_TITLE
    if len(cleaned) <= MAX_TITLE_LENGTH:
        return cleaned
    return cleaned[:MAX_TITLE_LENGTH] + "…"


class Database:
    """一个 SQLite 数据库的薄封装。"""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self.engine: Engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(self.engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        self._session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True
        )
        self.init_schema()

    # --- 基础设施 ---

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """事务上下文。异常时回滚。"""
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # --- 会话 ---

    def create_conversation(self, title: str | None = None) -> Conversation:
        conversation = Conversation(
            id=new_conversation_id(),
            title=(title or DEFAULT_TITLE)[:200],
        )
        with self.session() as session:
            session.add(conversation)
            session.flush()
            session.refresh(conversation)
            return conversation

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        with self.session() as session:
            return session.get(Conversation, conversation_id)

    def list_conversations(self) -> list[tuple[Conversation, int]]:
        """返回按更新时间倒序排列的 (会话, 消息数)。"""
        message_count = (
            select(func.count(Message.id))
            .where(Message.conversation_id == Conversation.id)
            .scalar_subquery()
        )
        stmt = select(Conversation, message_count).order_by(
            Conversation.updated_at.desc(), Conversation.id.desc()
        )
        with self.session() as session:
            return [(conversation, int(count)) for conversation, count in session.execute(stmt)]

    def delete_conversation(self, conversation_id: str) -> bool:
        with self.session() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return False
            session.delete(conversation)
            return True

    # --- 消息 ---

    def add_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        *,
        emotion_category: str | None = None,
        confidence: float | None = None,
        emotion_type: str | None = None,
        response_strategy: str | None = None,
    ) -> Message:
        with self.session() as session:
            message = Message(
                conversation_id=conversation_id,
                role=role,
                content=content,
                emotion_category=emotion_category,
                confidence=confidence,
                emotion_type=emotion_type,
                response_strategy=response_strategy,
            )
            session.add(message)
            conversation = session.get(Conversation, conversation_id)
            if conversation is not None:
                conversation.updated_at = utcnow()
            session.flush()
            session.refresh(message)
            return message

    def get_messages(self, conversation_id: str) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        )
        with self.session() as session:
            return list(session.execute(stmt).scalars())

    def get_history_pairs(self, conversation_id: str) -> list[dict[str, str]]:
        """以 ``{"role", "content"}`` 形式返回完整历史（不截断）。"""
        return [
            {"role": message.role, "content": message.content}
            for message in self.get_messages(conversation_id)
        ]

    def close(self) -> None:
        self.engine.dispose()
