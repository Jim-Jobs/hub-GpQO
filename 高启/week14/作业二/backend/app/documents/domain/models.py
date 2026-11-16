"""文档任务、解析状态和向量索引数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from backend.app.core.serialization import from_json_values, to_json


class DocumentStatus(str, Enum):
    """描述文档从等待处理到完成或失败的持久化状态。"""

    PENDING = "pending"
    PARSING = "parsing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class IngestEvent:
    """Kafka 中传递的单 PDF 任务元数据，不包含文件二进制。"""

    event_id: str
    document_id: str
    file_name: str
    relative_path: str
    created_at: datetime
    attempt: int = 0

    def to_json(self) -> str:
        """生成可写入 Kafka 的 JSON 任务消息。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> IngestEvent:
        """将 Kafka JSON 消息还原为任务事件。"""
        values = from_json_values(payload)
        values["created_at"] = datetime.fromisoformat(values["created_at"])
        return cls(**values)


@dataclass(frozen=True)
class DocumentRecord:
    """Redis 中保存的文档状态、进度和解析摘要。"""

    document_id: str
    file_name: str
    status: DocumentStatus
    progress: int = 0
    error: str | None = None
    title: str | None = None
    summary: str | None = None
    parsed_at: datetime | None = None

    def to_json(self) -> str:
        """生成可写入 Redis 的文档状态 JSON。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> DocumentRecord:
        """将 Redis JSON 状态还原为带枚举和时间类型的记录。"""
        values = from_json_values(payload)
        values["status"] = DocumentStatus(values["status"])
        if values.get("parsed_at"):
            values["parsed_at"] = datetime.fromisoformat(values["parsed_at"])
        return cls(**values)


@dataclass(frozen=True)
class DocumentSummary:
    """由文档摘要模型生成并在处理成功后写入状态的标题与摘要。"""

    title: str
    summary: str


@dataclass(frozen=True)
class CorpusProfile:
    """由已完成文档标题和摘要组成的资料库范围说明。"""

    document_count: int
    summary: str

    def to_json(self) -> str:
        """生成可写入 Redis 的资料库概览 JSON。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> CorpusProfile:
        """从 Redis JSON 恢复资料库概览。"""
        return cls(**from_json_values(payload))


@dataclass(frozen=True)
class ChunkRecord:
    """Milvus 检索使用的文本块及其来源页码和图片资源关联。"""

    chunk_id: str
    document_id: str
    page_number: int
    chunk_index: int
    text: str
    asset_ids: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        """生成包含文档、页码和图片资源关联的 JSON。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> ChunkRecord:
        """从 JSON 恢复一个检索文本块。"""
        return cls(**from_json_values(payload))


@dataclass(frozen=True)
class ChunkSearchHit:
    """Milvus 返回的文本块和 COSINE 相似度分数。"""

    chunk: ChunkRecord
    score: float


@dataclass(frozen=True)
class VectorRecord:
    """待写入 Milvus 的文本块及其 embedding 向量。"""

    chunk: ChunkRecord
    embedding: list[float]

    def to_json(self) -> str:
        """生成包含文本块与向量的 JSON 表示。"""
        return to_json(self)

    @classmethod
    def from_json(cls, payload: str) -> VectorRecord:
        """从 JSON 恢复向量记录及嵌套的文本块。"""
        values = from_json_values(payload)
        values["chunk"] = ChunkRecord(**values["chunk"])
        return cls(**values)


@dataclass(frozen=True)
class ImageAsset:
    """文档解析导出的图片及其文档内相对路径。"""

    asset_id: str
    document_id: str
    relative_path: str
    page_number: int | None = None


@dataclass(frozen=True)
class ParsedPage:
    """MinerU 结构化输出中的单页文本和图片引用，页码从 1 开始。"""

    page_number: int
    text: str
    asset_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedDocument:
    """供后续切块和资源服务使用的归一化解析结果。"""

    document_id: str
    markdown: str
    pages: list[ParsedPage]
    assets: list[ImageAsset] = field(default_factory=list)
