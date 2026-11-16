"""问答 Agent 节点间使用的路由、证据和检索数据契约。"""

from dataclasses import dataclass
from collections.abc import Callable
from typing import Protocol

from backend.app.chat.domain.enums import ChatStage
from backend.app.chat.domain.models import ChatImage
from backend.app.documents.domain.models import (
    ChunkRecord,
    ChunkSearchHit,
    CorpusProfile,
    DocumentRecord,
)


@dataclass(frozen=True)
class RouteAndQuery:
    """范围判断结果及范围内问题的文本检索表达。"""

    route: str
    search_query: str
    direct_answer: str = ""


@dataclass(frozen=True)
class RetrievedChunk:
    """带文件名和相似度的检索块，用于模型证据和引用生成。"""

    chunk: ChunkRecord
    score: float
    file_name: str


@dataclass(frozen=True)
class RetrievedImage:
    """已验证并可发送给模型的 PDF 图片及其来源信息。"""

    document_id: str
    asset_id: str
    page_number: int
    file_name: str
    image: ChatImage


@dataclass(frozen=True)
class EvidenceAssessment:
    """Qwen3-VL 对检索文本是否足以支撑回答的判断。"""

    sufficient: bool
    reason: str


class ChatModelClient(Protocol):
    """描述问答模型适配器的同步文本/图像调用接口。"""

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[ChatImage] | None = None,
    ) -> str:
        """提交提示词和可选图像并返回模型文本。"""


class ChatVectorStore(Protocol):
    """描述问答检索所需的 Milvus 搜索操作。"""

    def search_chunks(
        self, embedding: list[float], document_ids: list[str], limit: int
    ) -> list[ChunkSearchHit]:
        """只在给定完成文档范围内返回 Top-K 文本块。"""


class CompletedDocumentStore(Protocol):
    """描述问答读取完成文档和资料库概览所需的存储操作。"""

    def list_completed_documents(self) -> list[DocumentRecord]:
        """返回当前所有 completed 文档状态。"""

    def get_corpus_profile(self) -> CorpusProfile | None:
        """返回可供范围判断的资料库概览。"""


class ImageLoader(Protocol):
    """描述受控的 PDF 图片资源读取操作。"""

    def __call__(self, document_id: str, asset_id: str) -> ChatImage | None:
        """只返回确属该文档的图片内容；未知资源返回 None。"""


StageCallback = Callable[[ChatStage, int], None]
