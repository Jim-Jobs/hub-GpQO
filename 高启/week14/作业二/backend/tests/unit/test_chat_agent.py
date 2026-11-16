"""ChatAgent 范围判断、completed 检索、证据门控和来源验证测试。"""

import json
from datetime import datetime, timezone

import pytest

from backend.app.chat.domain.enums import ChatStage
from backend.app.chat.domain.models import ChatImage
from backend.app.documents.domain.models import (
    ChunkRecord,
    ChunkSearchHit,
    DocumentRecord,
    DocumentStatus,
)
from backend.app.agents.chat.agent import ChatAgent
from backend.app.agents.chat.state import ChatState
from backend.app.agents.chat.nodes.retrieve import retrieve_chunks


class FakeChatModel:
    """按预设顺序返回模型 JSON，并记录每阶段收到的图片。"""

    def __init__(self, responses: list[str]) -> None:
        """保存模型返回值队列和请求记录。"""
        self.responses = list(responses)
        self.calls: list[dict] = []

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[ChatImage] | None = None,
    ) -> str:
        """记录提示词/图片并返回下一条固定响应。"""
        self.calls.append(
            {"system": system_prompt, "user": user_prompt, "images": list(images or [])}
        )
        return self.responses.pop(0)


class FakeEmbeddingClient:
    """记录查询文本并返回一个符合 BGE-M3 契约的向量。"""

    def __init__(self) -> None:
        """初始化查询记录。"""
        self.texts: list[str] = []

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """保存输入文本并为每条输入返回 1024 维测试向量。"""
        self.texts.extend(texts)
        return [[0.25] * 1024 for _ in texts]


class FakeDocumentStore:
    """向 ChatAgent 提供已完成与未完成的文档记录。"""

    def __init__(self, records: list[DocumentRecord]) -> None:
        """保存待返回的文档记录。"""
        self.records = records

    def list_completed_documents(self) -> list[DocumentRecord]:
        """返回所有状态，供 Agent 验证只把 completed ID 传给 Milvus。"""
        return self.records


class FakeVectorStore:
    """记录检索参数并返回预设的 Milvus 命中。"""

    def __init__(self, hits: list[ChunkSearchHit]) -> None:
        """保存候选命中和空的检索调用记录。"""
        self.hits = hits
        self.calls: list[dict] = []

    def search_chunks(
        self, embedding: list[float], document_ids: list[str], limit: int
    ) -> list[ChunkSearchHit]:
        """记录向量、文档范围和 Top-K 后返回候选结果。"""
        self.calls.append(
            {"embedding": embedding, "document_ids": document_ids, "limit": limit}
        )
        return self.hits[:limit]


def _document_records() -> list[DocumentRecord]:
    """建立一个 completed、一个 parsing 和一个 failed 文档。"""
    return [
        DocumentRecord("doc-a", "a.pdf", DocumentStatus.COMPLETED, summary="资料主题"),
        DocumentRecord("doc-b", "b.pdf", DocumentStatus.PARSING),
        DocumentRecord("doc-c", "c.pdf", DocumentStatus.FAILED),
    ]


def _hits(count: int = 5) -> list[ChunkSearchHit]:
    """建立带稳定页码和图片关联的文本检索结果。"""
    return [
        ChunkSearchHit(
            ChunkRecord(
                f"chunk-{index}",
                "doc-a",
                index + 1,
                0,
                f"第 {index + 1} 页证据正文",
                [f"asset-{index}"],
            ),
            0.9 - index / 10,
        )
        for index in range(count)
    ]


def test_out_of_scope_question_skips_embedding_and_milvus() -> None:
    """范围外问题直接返回通用回答，不访问 embedding 或向量库。"""
    model = FakeChatModel(
        [
            json.dumps(
                {
                    "route": "out_of_scope",
                    "search_query": "",
                    "direct_answer": "这里是一首春天的小诗。可继续询问文档中的 QA 类型。",
                }
            )
        ]
    )
    embedding = FakeEmbeddingClient()
    vector_store = FakeVectorStore([])
    agent = ChatAgent(
        model,
        embedding,
        vector_store,
        FakeDocumentStore(_document_records()),
    )

    response = agent.run(ChatState("写一首春天的诗", corpus_profile="介绍五种 QA"))

    assert response.route == "out_of_scope"
    assert response.answer_segments[0]["type"] == "text"
    assert embedding.texts == []
    assert vector_store.calls == []


@pytest.mark.parametrize("limit", [0, -1])
def test_non_positive_retrieval_limit_fails_before_embedding(limit: int) -> None:
    """无效 Top-K 是参数错误，不能先花费 embedding 请求再返回空结果。"""
    embedding = FakeEmbeddingClient()
    vector_store = FakeVectorStore([])

    with pytest.raises(ValueError, match="limit"):
        retrieve_chunks(
            "检索问题",
            embedding,
            vector_store,
            FakeDocumentStore(_document_records()),
            limit=limit,
        )

    assert embedding.texts == []
    assert vector_store.calls == []


def test_in_scope_answer_searches_top_five_completed_docs_and_validates_sources() -> None:
    """范围内回答只搜 completed 文档，向模型最多传四图并过滤伪造来源。"""
    model = FakeChatModel(
        [
            json.dumps(
                {"route": "in_scope", "search_query": "手机型号无线充电属性", "direct_answer": ""}
            ),
            json.dumps({"sufficient": True, "reason": "第 1 页证据直接回答问题"}),
            json.dumps(
                {
                    "segments": [
                        {"type": "text", "text": "文档中的结论是支持。"},
                        {"type": "image", "asset_id": "asset-0"},
                        {"type": "image", "asset_id": "asset-forged"},
                    ],
                    "citation_chunk_ids": ["chunk-0", "chunk-forged"],
                }
            ),
        ]
    )
    embedding = FakeEmbeddingClient()
    vector_store = FakeVectorStore(_hits(8))
    loaded_assets: list[str] = []

    def load_image(document_id: str, asset_id: str) -> ChatImage | None:
        """返回测试图片并记录 Agent 请求读取的资源 ID。"""
        loaded_assets.append(asset_id)
        return ChatImage(b"pdf-image", "image/png")

    agent = ChatAgent(
        model,
        embedding,
        vector_store,
        FakeDocumentStore(_document_records()),
        image_loader=load_image,
    )
    user_image = ChatImage(b"user-image", "image/jpeg")

    stages = []
    response = agent.run(
        ChatState(
            "图中手机支持无线充电吗？",
            corpus_profile="资料主题",
            user_image=user_image,
        ),
        stage_callback=lambda stage, count: stages.append((stage, count)),
    )

    assert response.route == "in_scope"
    assert embedding.texts == ["手机型号无线充电属性"]
    assert vector_store.calls[0]["document_ids"] == ["doc-a"]
    assert vector_store.calls[0]["limit"] == 5
    assert len(response.citations) == 1
    assert response.citations[0]["chunk_id"] == "chunk-0"
    assert len(response.image_assets) == 1
    assert response.image_assets[0]["asset_id"] == "asset-0"
    assert loaded_assets == ["asset-0", "asset-1", "asset-2"]
    assert len(model.calls[0]["images"]) == 1
    assert model.calls[1]["images"] == []
    assert len(model.calls[2]["images"]) == 4
    assert stages == [
        (ChatStage.SCOPE_AND_REWRITE, 0),
        (ChatStage.RETRIEVAL, 0),
        (ChatStage.EVIDENCE_CHECK, 5),
        (ChatStage.ANSWER_GENERATION, 5),
    ]


def test_insufficient_text_evidence_returns_confirmation_without_answer_call() -> None:
    """证据判断不足时返回 needs_confirmation，不调用答案生成模型。"""
    model = FakeChatModel(
        [
            json.dumps(
                {"route": "in_scope", "search_query": "会员七项特权名称", "direct_answer": ""}
            ),
            json.dumps({"sufficient": False, "reason": "检索内容没有列出特权名称"}),
        ]
    )
    agent = ChatAgent(
        model,
        FakeEmbeddingClient(),
        FakeVectorStore(_hits(2)),
        FakeDocumentStore(_document_records()),
    )

    stages = []
    response = agent.run(
        ChatState("七项特权叫什么？", corpus_profile="会员资料"),
        stage_callback=lambda stage, count: stages.append((stage, count)),
    )

    assert response.route == "needs_confirmation"
    assert response.citations == []
    assert len(model.calls) == 2
    assert "没有找到" in response.answer_segments[0]["text"]
    assert stages[-1] == (ChatStage.AWAITING_CONFIRMATION, 2)


def test_general_answer_uses_reuploaded_content_without_retrieval() -> None:
    """用户接受通用回答后仅调用生成模型，并继续传入重新提交的图片。"""
    model = FakeChatModel([json.dumps({"text": "根据通用知识，这是一种常见做法。"})])
    agent = ChatAgent(
        model,
        FakeEmbeddingClient(),
        FakeVectorStore([]),
        FakeDocumentStore(_document_records()),
    )

    response = agent.answer_general("这项技术通常如何使用？", ChatImage(b"img", "image/png"))

    assert response.route == "general"
    assert response.citations == []
    assert response.answer_segments == [
        {"type": "text", "text": "根据通用知识，这是一种常见做法。"}
    ]
    assert len(model.calls) == 1
    assert model.calls[0]["images"] == [ChatImage(b"img", "image/png")]
