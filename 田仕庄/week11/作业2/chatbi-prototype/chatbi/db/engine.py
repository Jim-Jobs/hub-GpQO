"""ORM 引擎与会话工厂。

默认与语义层运行时库共用 ``config.META_DB``（``data/chatbi_meta.db``），
``MetaStore`` 即基于本模块的引擎读写同一份库。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import META_DB
from .base import Base

ORM_DB = Path(os.environ.get("CHATBI_ORM_DB", str(META_DB)))

_engine: Optional[Engine] = None
_factory: Optional[sessionmaker] = None


def orm_url(path: str | Path | None = None) -> str:
    p = Path(path or ORM_DB)
    if p.parent and str(p.parent) not in ("", "."):
        p.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{p}"


def _make_engine(url: str) -> Engine:
    eng = create_engine(url, future=True)
    if url.startswith("sqlite"):
        # SQLite 默认关闭外键约束；WAL 让读写互不阻塞（元数据与会话共用同一库）
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - 连接级回调
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    return eng


def engine_for(path: str | Path) -> Engine:
    """为指定 SQLite 文件创建独立引擎（用于临时库 / 演示库）。"""
    return _make_engine(orm_url(path))


def get_engine(url: str | None = None) -> Engine:
    global _engine
    if url:
        return _make_engine(url)
    if _engine is None:
        _engine = _make_engine(orm_url())
    return _engine


def get_session_factory(url: str | None = None) -> sessionmaker:
    global _factory
    if url:
        return sessionmaker(bind=_make_engine(url), expire_on_commit=False, future=True)
    if _factory is None:
        _factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _factory


@contextmanager
def session_scope(url: str | None = None) -> Iterator[Session]:
    """事务边界：正常提交，异常回滚。"""
    factory = get_session_factory(url)
    db = factory()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(url: str | None = None) -> Engine:
    """建表（幂等）。会先导入 models 以注册全部表。"""
    from . import models  # noqa: F401  注册 metadata

    eng = get_engine(url)
    Base.metadata.create_all(eng)
    return eng
