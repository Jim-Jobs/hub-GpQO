"""文档元数据：SQLite + SQLAlchemy 单表。"""

import datetime as dt

from sqlalchemy import DateTime, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from mrag.config import DATA_DIR, DB_PATH

# 状态机：pending -> parsing -> done | failed
STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
ALL_STATUSES = (STATUS_PENDING, STATUS_PARSING, STATUS_DONE, STATUS_FAILED)


class Base(DeclarativeBase):
    pass


class FileRow(Base):
    """一个上传的 PDF 及其解析状态。"""

    __tablename__ = "files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(512))  # 用户原始文件名
    file_path: Mapped[str] = mapped_column(String(1024))  # data/uploads/<uuid>.pdf
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING)
    error: Mapped[str] = mapped_column(String(2048), default="")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=lambda: dt.datetime.now(dt.UTC))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime,
        default=lambda: dt.datetime.now(dt.UTC),
        onupdate=lambda: dt.datetime.now(dt.UTC),
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "status": self.status,
            "error": self.error,
            "chunk_count": self.chunk_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


def get_engine():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


def init_db() -> sessionmaker:
    engine = get_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
