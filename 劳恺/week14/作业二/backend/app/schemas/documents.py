"""文档相关 API 的出入参模型。

对应文档：``docs/04-API契约.md`` §2 —— **前端已按此开发，字段名不可改**。
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    """单个文档（列表项与详情共用，``docs/04`` §2.2）。"""

    doc_id: str
    filename: str
    size_bytes: int = 0
    page_count: int | None = None
    status: str
    progress: float = 0.0
    stage: str = "queued"
    error: str | None = None
    chunk_count: int = 0
    image_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None


class AcceptedFile(BaseModel):
    doc_id: str
    filename: str
    status: str = "pending"
    message: str = "已入队，等待解析"


class RejectedFile(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    """``POST /api/documents`` 的 202 响应（部分成功语义）。"""

    accepted: list[AcceptedFile] = Field(default_factory=list)
    rejected: list[RejectedFile] = Field(default_factory=list)


class DeleteResponse(BaseModel):
    doc_id: str
    deleted: bool = True


class RetryResponse(BaseModel):
    doc_id: str
    status: str = "pending"
    message: str = "已重新入队"


class SessionSummary(BaseModel):
    session_id: str
    title: str = ""
    message_count: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None
