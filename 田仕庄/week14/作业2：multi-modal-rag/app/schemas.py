"""Pydantic 请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    version: str
    mock: bool
    parse_backend: str
    db: str
    log_dir: str


class DependencyStatus(BaseModel):
    ok: bool
    detail: str


class DepsResponse(BaseModel):
    status: str
    deps: dict[str, DependencyStatus]


# ---------------------------------------------------------------------------
# 上传
# ---------------------------------------------------------------------------
class UploadItemResult(BaseModel):
    filename: str
    file_id: Optional[int] = None
    status: str = Field(description="PENDING | FAILED | DUPLICATE | REJECTED")
    status_text: str
    action: str = Field(description="queued | duplicate | rejected")
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    existing_file_id: Optional[int] = None


class UploadBatchResponse(BaseModel):
    batch_id: str
    knowledge_base_id: str
    accepted: int
    rejected: int
    duplicates: int
    items: list[UploadItemResult]


class UploadDocumentResponse(BaseModel):
    """兼容旧版单文件上传。"""

    file_id: int
    filename: str
    knowledge_base_id: str
    status: str = "queued"


# ---------------------------------------------------------------------------
# 文档列表 / 状态
# ---------------------------------------------------------------------------
class DocumentItem(BaseModel):
    file_id: int
    filename: str
    status: str
    status_text: str
    knowledge_base_id: str
    batch_id: Optional[str] = None
    filepath: str
    size_bytes: Optional[int] = None
    page_count: Optional[int] = None
    chunk_count: Optional[int] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    attempts: int = 0
    kafka_sent: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    elapsed_sec: Optional[float] = None


class DocumentListResponse(BaseModel):
    knowledge_base_id: str
    total: int
    items: list[DocumentItem]


class StatusItem(BaseModel):
    file_id: int
    status: str
    status_text: str
    updated_at: Optional[datetime] = None


class StatusBatchResponse(BaseModel):
    server_time: datetime
    items: list[StatusItem]


class ReparseResponse(BaseModel):
    file_id: int
    status: str
    status_text: str
    message: str


class DeleteDocumentResponse(BaseModel):
    file_id: int
    status: str = Field(description="deleted | not_found")


class LegacyDocumentItem(BaseModel):
    file_id: int
    filename: str
    filestate: str
    status: str
    knowledge_base_id: str
    filepath: str


class LegacyDocumentListResponse(BaseModel):
    knowledge_base_id: str
    documents: list[LegacyDocumentItem]


# ---------------------------------------------------------------------------
# 问答
# ---------------------------------------------------------------------------
class ChatRequest(BaseModel):
    question: str = Field(default="", max_length=4000)
    knowledge_base_id: str = "default"
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    use_clip: bool = True


class SourceItem(BaseModel):
    file_id: int
    file_name: str
    file_path: str
    text_snippet: str
    page: Optional[str] = None
    image_urls: list[str] = []
    retrieval_method: Optional[str] = None
    score: Optional[float] = None


class UsedImage(BaseModel):
    slot: int
    label: str
    source: str = Field(description="user | retrieved")
    file_id: Optional[int] = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceItem] = []
    used_images: list[UsedImage] = []
    degraded: dict[str, float | int] = {}
    timings: dict[str, float] = {}
    trace_id: str


# ---------------------------------------------------------------------------
# 知识库鉴权
# ---------------------------------------------------------------------------
class ApiKeyCreateRequest(BaseModel):
    knowledge_base_id: str
    api_key: str = Field(min_length=6, max_length=128)
    role: str = Field(default="write", pattern="^(read|write|admin)$")


class ApiKeyCreateResponse(BaseModel):
    id: int
    knowledge_base_id: str
    role: str
    status: str = "created"
