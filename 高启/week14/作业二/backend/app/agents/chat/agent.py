"""按显式 Python 节点顺序执行多模态问答流程。"""

from __future__ import annotations

from importlib.resources import files

from backend.app.agents.chat.models import (
    ChatModelClient,
    ChatVectorStore,
    ImageLoader,
    StageCallback,
)
from backend.app.agents.chat.nodes.evaluate_evidence import evaluate_evidence
from backend.app.agents.chat.nodes.generate_answer import generate_answer
from backend.app.agents.chat.nodes.generate_general_answer import generate_general_answer
from backend.app.agents.chat.nodes.retrieve import retrieve_chunks
from backend.app.agents.chat.nodes.scope_and_rewrite import scope_and_rewrite
from backend.app.agents.chat.state import ChatState
from backend.app.chat.domain.enums import ChatStage
from backend.app.chat.domain.models import ChatImage, ChatResponse
from backend.app.core.config import Settings
from backend.app.documents.application.ports import DocumentStore
from backend.app.documents.parsing.asset_registry import load_asset_bytes
from backend.app.documents.indexing.embed_chunks import TextEmbeddingClient
from backend.app.integrations.embedding_client import OpenAIEmbeddingClient
from backend.app.integrations.milvus_store import MilvusChunkStore
from backend.app.integrations.vllm_chat import OpenAICompatibleChatClient


class ChatAgent:
    """持有共享模型与存储依赖，并按范围、检索、证据和回答调度节点。"""

    def __init__(
        self,
        model: ChatModelClient,
        embedding_client: TextEmbeddingClient,
        vector_store: ChatVectorStore,
        document_store: DocumentStore,
        *,
        settings: Settings | None = None,
        image_loader: ImageLoader | None = None,
    ) -> None:
        """保存单进程共享依赖；未注入图片读取器时从受控解析目录加载。"""
        self._model = model
        self._embedding_client = embedding_client
        self._vector_store = vector_store
        self._document_store = document_store
        self._settings = settings
        self._image_loader = image_loader
        self._scope_prompt = _read_prompt("scope_and_rewrite.md")
        self._evidence_prompt = _read_prompt("evaluate_evidence.md")
        self._answer_prompt = _read_prompt("generate_answer.md")

    @classmethod
    def from_settings(cls, settings: Settings, document_store: DocumentStore) -> ChatAgent:
        """构建真实 vLLM、BGE-M3 和共享 Milvus 依赖供应用组合根使用。"""
        settings.initialize_data_directories()
        return cls(
            OpenAICompatibleChatClient(settings.chat_base_url or "", settings.chat_model),
            OpenAIEmbeddingClient(settings.embedding_base_url or "", settings.embedding_model),
            MilvusChunkStore.from_path(settings.milvus_path),
            document_store,
            settings=settings,
        )

    def run(
        self, state: ChatState, stage_callback: StageCallback | None = None
    ) -> ChatResponse:
        """执行问答节点；范围外不检索，证据不足时停在确认分支。"""
        if stage_callback is not None:
            stage_callback(ChatStage.SCOPE_AND_REWRITE, 0)
        route = scope_and_rewrite(
            state.question_text,
            state.user_image,
            state.corpus_profile,
            self._model,
            self._scope_prompt,
        )
        state.route_and_query = route
        if route.route == "out_of_scope":
            state.response = ChatResponse(
                route="out_of_scope",
                answer_segments=[{"type": "text", "text": route.direct_answer}],
            )
            return state.response

        if stage_callback is not None:
            stage_callback(ChatStage.RETRIEVAL, 0)
        state.retrieved_chunks = retrieve_chunks(
            route.search_query,
            self._embedding_client,
            self._vector_store,
            self._document_store,
        )
        if stage_callback is not None:
            stage_callback(ChatStage.EVIDENCE_CHECK, len(state.retrieved_chunks))
        state.evidence = evaluate_evidence(
            state.question_text,
            state.retrieved_chunks,
            self._model,
            self._evidence_prompt,
        )
        if not state.evidence.sufficient:
            if stage_callback is not None:
                stage_callback(ChatStage.AWAITING_CONFIRMATION, len(state.retrieved_chunks))
            state.response = ChatResponse(
                route="needs_confirmation",
                answer_segments=[
                    {
                        "type": "text",
                        "text": "资料中没有找到足以回答该问题的依据。"
                        "你希望我根据通用知识继续回答吗？",
                    }
                ],
            )
            return state.response

        if stage_callback is not None:
            stage_callback(ChatStage.ANSWER_GENERATION, len(state.retrieved_chunks))
        state.response = generate_answer(
            state.question_text,
            state.user_image,
            state.retrieved_chunks,
            self._model,
            self._answer_prompt,
            self._load_image,
        )
        return state.response

    def answer_general(self, question_text: str, user_image: ChatImage | None) -> ChatResponse:
        """只基于重传的问题和图片生成通用回答，不执行检索或引用文档。"""
        return generate_general_answer(
            question_text,
            user_image,
            self._model,
            _read_prompt("generate_general_answer.md"),
        )

    def _load_image(self, document_id: str, asset_id: str) -> ChatImage | None:
        """优先使用测试替身，否则从该 document_id 的受控目录读取图片。"""
        if self._image_loader is not None:
            return self._image_loader(document_id, asset_id)
        if self._settings is None:
            return None
        loaded = load_asset_bytes(self._settings.parsed_dir, document_id, asset_id)
        if loaded is None:
            return None
        data, media_type = loaded
        return ChatImage(data, media_type)


def _read_prompt(file_name: str) -> str:
    """从 prompts/chat 包读取单个节点模板，避免在流程代码中硬编码提示词。"""
    return files("backend.app.prompts.chat").joinpath(file_name).read_text(encoding="utf-8")
