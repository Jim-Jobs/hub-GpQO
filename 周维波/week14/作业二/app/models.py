"""SQLAlchemy 数据模型：文档元信息 + 切片元信息 + 本地向量表。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .status import STATUS_PENDING


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    """一个上传的 PDF 文档及其解析状态。"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    parsed_dir: Mapped[str | None] = mapped_column(String(1024), default=None)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    file_type: Mapped[str] = mapped_column(String(16), default="pdf")

    # 状态机：pending -> parsing -> completed / failed
    status: Mapped[str] = mapped_column(String(16), default=STATUS_PENDING, index=True)
    stage: Mapped[str] = mapped_column(String(64), default="排队中")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    attempts: Mapped[int] = mapped_column(Integer, default=0)

    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    image_count: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Chunk(Base):
    """切片元信息（文本、页码、关联图片），向量放在向量库 / 本地向量表。"""

    __tablename__ = "chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    doc_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer, default=None)
    image_rel: Mapped[str | None] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (Index("ix_chunks_doc_seq", "doc_id", "seq"),)


class ChunkVector(Base):
    """本地（sqlite）向量存储：把 float32 向量序列化成 BLOB。

    VECTOR_STORE=sqlite 时启用，用于零依赖跑通全链路；
    生产切到 Milvus 后这张表为空。
    """

    __tablename__ = "chunk_vectors"

    chunk_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(32), index=True)
    text_vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    clip_text_vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    clip_image_vector: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
