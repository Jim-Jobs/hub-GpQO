"""问答服务：检索 → 组 Prompt → 流式生成 → 引用解析 → 会话落库。

对应文档：``docs/01-架构设计.md` §3.3、``docs/06-RAG与多模态设计.md`。

**问答不走 Kafka** —— 交互式场景要求首字 < 3s，消息队列的分区/offset/消费组语义
在这里是净负担。

流程::

    用户问题
      ├─ embed(query) → query_vector
      ├─ 三路检索（BM25 + chunk 向量 + 图片向量）→ RRF → top-8
      ├─ 从 top-8 里挑 ≤4 个带图的块，收集 ImageRef
      ├─ 组装图文交替 Prompt（用户图在前）
      ├─ vLLM / 外部 API stream=true
      ├─ 逐 token 推 delta，边流边识别 [[cite:...]] → 推 citation
      └─ done（完整答案 + citations + 命中 chunk_id）
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.llm.citation import CitationEvent, CitationParser, ImageMeta
from app.llm.embedding_client import EmbeddingClient
from app.llm.image_utils import compress_to_data_url, strip_data_url_prefix
from app.llm.prompts import PromptChunk, PromptImage, build_chat_prompt
from app.llm.vllm_client import LLMClient
from app.retrieval.hybrid_search import HybridSearcher, RetrievedChunk
from app.schemas.chat import (
    ChatCitation,
    ChatDelta,
    ChatDone,
    ChatError,
    ChatRequest,
    CitationPayload,
)
from app.schemas.index import new_session_id, utcnow
from app.storage.es_client import ESClient
from app.storage.index_schema import INDEX_SESSIONS

logger = logging.getLogger(__name__)


@dataclass
class ChatOutcome:
    """一问一答的完整结果（用于落库）。"""

    session_id: str
    answer: str
    citations: list[CitationPayload]
    retrieved_chunk_ids: list[str]


class ChatService:
    """问答编排。"""

    def __init__(
        self,
        settings: Settings,
        es: ESClient,
        embedder: EmbeddingClient,
        llm: LLMClient,
    ) -> None:
        self._settings = settings
        self._es = es
        self._embedder = embedder
        self._llm = llm
        self._searcher = HybridSearcher(es, embedder, settings)

    # ---------- 主流程 ----------

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[str]:
        """产出 SSE 帧。错误以 ``error`` 事件呈现，不抛异常给框架。"""
        session_id = request.session_id or new_session_id()
        try:
            async for frame in self._stream_chat(request, session_id):
                yield frame
        except Exception as exc:  # noqa: BLE001 —— SSE 里用 error 事件表达失败
            logger.exception("问答失败 session=%s", session_id)
            error = ChatError(
                code="chat_failed",
                message=f"回答生成失败: {exc}",
            )
            yield _sse("error", error.model_dump())

    async def _stream_chat(
        self, request: ChatRequest, session_id: str
    ) -> AsyncIterator[str]:
        settings = self._settings
        question = request.question.strip()
        if not question:
            error = ChatError(code="invalid_request", message="问题不能为空")
            yield _sse("error", error.model_dump())
            return

        # ① 检索
        chunks = await self._searcher.search(question, doc_ids=request.doc_ids)

        # ② 选图：用户图在前，文档图从 top-8 里挑（每块取第一张）
        user_images = await self._build_user_images(request)
        doc_images = self._select_doc_images(chunks)
        all_images = [*user_images, *doc_images]

        image_meta = [
            ImageMeta(
                image_id=image.image_id,
                index=position,
                url=image.url,
                doc_id=image.doc_id,
                doc_filename=image.doc_filename,
                page_no=image.page_no,
                caption=image.caption,
            )
            for position, image in enumerate(all_images, start=1)
        ]

        # ③ 组 Prompt
        await self._fill_doc_image_urls(doc_images)
        prompt_chunks = self._to_prompt_chunks(chunks, doc_images)
        history = await self._load_history(session_id)
        built = build_chat_prompt(question, prompt_chunks, user_images, history)

        # ④ 流式生成 + 引用解析
        parser = CitationParser(image_meta)
        answer_parts: list[str] = []
        async for delta in self._llm.chat_stream(
            built.messages,
            max_tokens=settings.llm_max_tokens,
            temperature=settings.llm_temperature,
        ):
            safe_text, events = parser.feed(delta)
            if safe_text:
                answer_parts.append(safe_text)
                yield _sse("delta", ChatDelta(text=safe_text).model_dump())
            for event in events:
                yield _sse("citation", self._citation_payload(event).model_dump())

        tail, events = parser.flush()
        if tail:
            answer_parts.append(tail)
            yield _sse("delta", ChatDelta(text=tail).model_dump())
        for event in events:
            yield _sse("citation", self._citation_payload(event).model_dump())

        answer = "".join(answer_parts)

        # ⑤ 引用降级：一个标记都没识别到 → citations 退化为"本次检索到的全部图片"
        citations = self._build_citations(parser, doc_images)
        payload = ChatDone(
            session_id=session_id,
            answer=answer,
            citations=citations,
            retrieved_chunk_ids=[chunk.chunk_id for chunk in chunks],
        )
        await self._save_session(session_id, request, answer, citations)
        yield _sse("done", payload.model_dump())

    # ---------- 检索结果的转换 ----------

    def _to_prompt_chunks(
        self, chunks: list[RetrievedChunk], doc_images: list[PromptImage]
    ) -> list[PromptChunk]:
        """把检索块转成 Prompt 块，并把选中的图片挂上去。

        **图片必须紧贴它所属的文字块**（docs/06 §5.1）。
        """
        image_by_chunk: dict[str, list[PromptImage]] = {}
        for image in doc_images:
            image_by_chunk.setdefault(image.chunk_id, []).append(image)

        result: list[PromptChunk] = []
        for chunk in chunks:
            result.append(
                PromptChunk(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text,
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    doc_filename=chunk.doc_filename,
                    images=image_by_chunk.get(chunk.chunk_id, []),
                )
            )
        return result

    def _select_doc_images(self, chunks: list[RetrievedChunk]) -> list[PromptImage]:
        """从 top-8 块里挑文档图。

        规则（docs/06 §4.2）：
        1. 筛出含图片的块（保持 RRF 顺序）；
        2. 取前 4 个，每个块取**第一张**图片；
        3. 某块有多张图且都是关键图时，最多再取 1 张。

        **预算硬上限 4** —— 与 vLLM ``--limit-mm-per-prompt '{"image":8}'`` 对齐。
        """
        selected: list[PromptImage] = []
        used_blocks = 0
        for chunk in chunks:
            if not chunk.images:
                continue
            if used_blocks >= self._settings.max_doc_images:
                break
            first = chunk.images[0]
            selected.append(
                PromptImage(
                    image_id=first.image_id,
                    data_url="",  # 稍后由 _fill_doc_image_urls 填充
                    page_no=first.page_no,
                    caption=first.caption,
                    doc_filename=chunk.doc_filename,
                    url=first.url,
                    doc_id=chunk.doc_id,
                    chunk_id=chunk.chunk_id,
                    path=first.path,
                )
            )
            used_blocks += 1
        return selected

    async def _build_user_images(self, request: ChatRequest) -> list[PromptImage]:
        """用户图片 → base64 data URL。最多 4 张，不落盘。

        数据校验已在 API 层做过（进流之前返回 400），这里只做转换。
        """
        images: list[PromptImage] = []
        for user_image in request.images[: self._settings.max_user_images]:
            data = strip_data_url_prefix(user_image.data)
            images.append(
                PromptImage(
                    image_id=f"user_{len(images) + 1}",
                    data_url=f"data:image/jpeg;base64,{data}",
                    is_user=True,
                )
            )
        return images

    async def _fill_doc_image_urls(self, doc_images: list[PromptImage]) -> None:
        """把磁盘上的图片压缩成 data URL。"""
        for image in doc_images:
            if image.data_url:
                continue
            path = self._settings.images_dir / image.path
            if not path.is_file():
                continue
            try:
                image.data_url = await _compress(path, self._settings)
            except Exception:  # noqa: BLE001 —— 单张图失败不应中断整次问答
                logger.warning("图片压缩失败: %s", path, exc_info=True)

    # ---------- 引用 ----------

    @staticmethod
    def _citation_payload(event: CitationEvent) -> ChatCitation:
        return ChatCitation(
            text=event.text,
            image_id=event.image_id,
            doc_id=event.doc_id,
            doc_filename=event.doc_filename,
            page_no=event.page_no,
            url=event.url,
            caption=event.caption,
        )

    def _build_citations(
        self, parser: CitationParser, doc_images: list[PromptImage]
    ) -> list[CitationPayload]:
        """组装 ``done.citations``。

        降级：**一个标记都没识别到** → 退化为"本次检索到的全部图片"
        （docs/04 §4「降级行为」）。
        """
        if parser.citations:
            return [
                CitationPayload(
                    image_id=item.image_id,
                    doc_id=item.doc_id,
                    doc_filename=item.doc_filename,
                    page_no=item.page_no,
                    url=item.url,
                    caption=item.caption,
                    text=item.text,
                )
                for item in parser.citations
            ]

        fallback = [
            CitationPayload(
                image_id=image.image_id,
                doc_id=image.doc_id,
                doc_filename=image.doc_filename,
                page_no=image.page_no,
                url=image.url,
                caption=image.caption,
                text="",
            )
            for image in doc_images
        ]
        return fallback

    # ---------- 会话 ----------

    async def _load_history(self, session_id: str) -> list[dict]:
        """取最近 N 轮历史。**历史图片不重传**（docs/06 §7）。"""
        stored = await self._es.get(INDEX_SESSIONS, session_id)
        if stored is None:
            return []
        messages = stored.get("messages") or []
        turns = self._settings.chat_history_turns
        recent = messages[-turns * 2 :] if turns > 0 else []
        history: list[dict] = []
        for message in recent:
            role = message.get("role")
            content = message.get("content")
            if role in ("user", "assistant") and isinstance(content, str) and content:
                history.append({"role": role, "content": content})
        return history

    async def _save_session(
        self,
        session_id: str,
        request: ChatRequest,
        answer: str,
        citations: list[CitationPayload],
    ) -> None:
        """把这一轮写进会话。

        **历史图片只存文件名与哈希，不存 base64**（docs/02 §2.5）。
        """
        stored = await self._es.get(INDEX_SESSIONS, session_id)
        messages: list[dict] = list((stored or {}).get("messages") or [])

        messages.append(
            {
                "role": "user",
                "content": request.question,
                "images": [
                    {"filename": image.filename, "sha256": ""}
                    for image in request.images
                ],
                "created_at": utcnow().isoformat(),
            }
        )
        messages.append(
            {
                "role": "assistant",
                "content": answer,
                "citations": [item.model_dump() for item in citations],
                "created_at": utcnow().isoformat(),
            }
        )

        title = ""
        for message in messages:
            if message.get("role") == "user":
                title = str(message.get("content", ""))[:60]
                break

        await self._es.index_document(
            INDEX_SESSIONS,
            session_id,
            {
                "session_id": session_id,
                "title": title,
                "messages": messages,
                "created_at": (stored or {}).get("created_at") or utcnow().isoformat(),
                "updated_at": utcnow().isoformat(),
            },
        )


async def _compress(path: Path, settings: Settings) -> str:
    import asyncio

    return await asyncio.to_thread(
        compress_to_data_url,
        path,
        max_edge=settings.image_max_edge,
        quality=settings.image_jpeg_quality,
    )


def _sse(event: str, data: dict) -> str:
    """SSE 帧。"""
    import json

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
