"""SQLite 连接与基础设施：WAL、Session、UTC 时间、建表入口。"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()
settings.db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{settings.db_path.as_posix()}",
    connect_args={"check_same_thread": False, "timeout": 30},
    future=True,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # pragma: no cover
    """API 与 Worker 双进程写同一个库时必须开 WAL，否则会 database is locked。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


Base = declarative_base()
Session = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    future=True,
)


def utcnow() -> datetime:
    """Naive UTC，便于 SQLite 存储与比较。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def init_db() -> None:
    """建表；后续字段演进在这里做幂等迁移。"""
    from app import models  # noqa: F401  确保模型已注册到 Base.metadata

    Base.metadata.create_all(engine)
    logger.debug("database ready: %s", settings.db_path)
