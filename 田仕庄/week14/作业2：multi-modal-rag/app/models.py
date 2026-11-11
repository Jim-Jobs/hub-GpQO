"""SQLAlchemy 模型。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Index, Integer, String

from app.db import Base, utcnow


class File(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    filepath = Column(String(1000), nullable=False)
    status = Column(String(20), nullable=False, default="PENDING", index=True)
    knowledge_base_id = Column(
        String(64), nullable=False, default="default", index=True
    )
    batch_id = Column(String(64), nullable=True, index=True)
    sha256 = Column(String(64), nullable=True, index=True)
    size_bytes = Column(Integer, nullable=True)
    page_count = Column(Integer, nullable=True)
    chunk_count = Column(Integer, nullable=True)
    error_code = Column(String(64), nullable=True)
    error_message = Column(String(1000), nullable=True)
    attempts = Column(Integer, nullable=False, default=0)
    requeue_count = Column(Integer, nullable=False, default=0)
    kafka_sent = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, nullable=False, default=utcnow)
    updated_at = Column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)


Index("ix_files_kb_status", File.knowledge_base_id, File.status)


class KbApiKey(Base):
    """知识库 API Key（哈希存储）。"""

    __tablename__ = "kb_api_keys"

    id = Column(Integer, primary_key=True)
    api_key_hash = Column(String(64), nullable=False, unique=True, index=True)
    knowledge_base_id = Column(String(64), nullable=False, index=True)
    role = Column(String(16), nullable=False, default="write")
