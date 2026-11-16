"""内部文档模型（ES 落库形态）。

对应文档：``docs/02-数据模型.md`` §4。

这些模型是「``content_list`` → 内部表示 → ES 文档」链路的中间层：
Pydantic 负责字段约束与序列化，``to_es()`` 产出可直接写进 ES 的 dict。

ID 规则（幂等的基础，见 ``docs/03`` §5.1）：

* ``chunk_id = {doc_id}_c{seq:04d}`` —— 序号由内容顺序决定，同内容必得同 ID
* ``image_id = {doc_id}_p{page_no}_i{idx}`` —— ``page_no`` 是 **1 基**
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

DocumentStatus = Literal["pending", "processing", "done", "failed"]

#: 解析阶段（``docs/03`` §3.2）
STAGES = (
    "queued",
    "mineru_parse",
    "chunking",
    "caption",
    "embedding",
    "indexing",
    "finished",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_doc_id() -> str:
    """``d_`` + 12 位 hex。"""
    import secrets

    return f"d_{secrets.token_hex(6)}"


def new_session_id() -> str:
    """``s_`` + 12 位 hex。"""
    import secrets

    return f"s_{secrets.token_hex(6)}"


def new_task_id() -> str:
    """``t_`` + 12 位 hex。"""
    import secrets

    return f"t_{secrets.token_hex(6)}"


def chunk_id_for(doc_id: str, seq: int) -> str:
    return f"{doc_id}_c{seq:04d}"


def image_id_for(doc_id: str, page_no: int, idx: int) -> str:
    return f"{doc_id}_p{page_no}_i{idx}"


class ImageRef(BaseModel):
    """图片引用（内嵌在 chunk 的 ``images`` 字段里）。"""

    image_id: str
    doc_id: str
    page_no: int
    idx: int
    path: str
    url: str
    caption: str = ""
    bbox: list[float] | None = None
    width: int | None = None
    height: int | None = None


class ChunkDoc(BaseModel):
    chunk_id: str
    doc_id: str
    doc_filename: str
    seq: int
    page_start: int
    page_end: int
    text: str
    tokens: str
    block_types: list[str] = Field(default_factory=list)
    bboxes: list[list[float]] = Field(default_factory=list)
    images: list[ImageRef] = Field(default_factory=list)
    vector: list[float] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    def to_es(self) -> dict:
        """转成 ES 文档体。

        **保留 ``chunk_id`` 字段** —— ``docs/02`` §2.2 的 mapping 里声明了它，
        检索层要用 ``{"terms": {"chunk_id": [...]}}`` 反查块，不能只靠 ``_id``。
        """
        return self.model_dump(mode="json")


class ImageDoc(BaseModel):
    image_id: str
    doc_id: str
    doc_filename: str
    chunk_id: str
    page_no: int
    idx: int
    path: str
    url: str
    caption: str = ""
    tokens: str = ""
    bbox: list[float] | None = None
    width: int | None = None
    height: int | None = None
    vector: list[float] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)

    def to_es(self) -> dict:
        """转成 ES 文档体。**保留 ``image_id`` 字段**（mapping 里声明了它）。"""
        return self.model_dump(mode="json")


class DocumentDoc(BaseModel):
    doc_id: str
    filename: str
    file_path: str
    content_hash: str = ""
    size_bytes: int = 0
    page_count: int | None = None
    status: DocumentStatus = "pending"
    progress: float = 0.0
    stage: str = "queued"
    error: str | None = None
    chunk_count: int = 0
    image_count: int = 0
    parser: str = "mineru"
    parse_duration_ms: int | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None

    def to_es(self) -> dict:
        """转成 ES 文档体。**保留 ``doc_id`` 字段**（mapping 里声明了它）。"""
        return self.model_dump(mode="json")
