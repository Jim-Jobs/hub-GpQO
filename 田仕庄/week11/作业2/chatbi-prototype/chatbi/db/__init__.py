"""ORM 数据访问层（SQLAlchemy 2.0）。

与 ``chatbi.metastore`` 并存：metastore 是手写 SQL 的原型实现，
本包是《04-数据模型ORM设计》落地后的目标实现，逐步替换 metastore。
"""
from .base import Base
from .engine import ORM_DB, get_engine, get_session_factory, init_db

__all__ = ["Base", "ORM_DB", "get_engine", "get_session_factory", "init_db"]
