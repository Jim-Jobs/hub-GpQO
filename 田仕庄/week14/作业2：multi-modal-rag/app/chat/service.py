"""问答编排：图片归一化 → 检索 → parts → 生成 → 来源。"""

from __future__ import annotations

import logging
import time
import uuid

from app.chat import llm, normalize, parts as parts_builder, retriever
from app.config import get_settings
from app.errors import QUESTION_EMPTY, RETRIEVAL_FAILED, AppError
from app.ingest import service
from app.logging_setup import trace_id_var
from app.schemas import ChatRequest, ChatResponse

logger = logging.getLogger(__name__)

_DEFAULT_IMAGE_QUESTION = "请描述图片内容，并结合知识库资料回答。"


def _trace_id() -> str:
    value = trace_id_var.get()
    return value if value != "-" else f"t_{uuid.uuid4().hex[:12]}"


def ask(
    request: ChatRequest,
    images: list[tuple[str, bytes]] | None = None,
) -> ChatResponse:
    settings = get_settings()
    trace_id = _trace_id()
    images = images or []
    original_question = request.question.strip()

    if not original_question and not images:
        raise AppError(QUESTION_EMPTY, status_code=400)

    started = time.perf_counter()
    normalized = []
    image_ms = 0.0
    if images:
        image_started = time.perf_counter()
        normalized = normalize.normalize_images(images, trace_id)
        image_ms = round((time.perf_counter() - image_started) * 1000, 1)

    question = original_question or _DEFAULT_IMAGE_QUESTION
    file_ids = service.list_done_file_ids(request.knowledge_base_id)

    hits: list[dict] = []
    retrieval_ms = 0.0
    if original_question and file_ids:
        retrieval_started = time.perf_counter()
        try:
            hits = retriever.retrieve(
                original_question,
                file_ids,
                top_k=request.top_k or settings.search_top_k,
                use_clip=request.use_clip,
            )
        except Exception as exc:
            logger.exception("retrieval failed")
            raise AppError(
                RETRIEVAL_FAILED, f"检索失败: {exc}", 503
            ) from exc
        retrieval_ms = round(
            (time.perf_counter() - retrieval_started) * 1000, 1
        )

    if not hits and not normalized:
        answer = (
            "知识库中暂无解析完成的文档，请先上传并等待解析完成。"
            if not file_ids
            else "未在知识库中找到与问题相关的内容。"
        )
        return ChatResponse(
            answer=answer,
            sources=[],
            used_images=[],
            degraded={},
            timings={"retrieval_ms": retrieval_ms, "image_ms": image_ms},
            trace_id=trace_id,
        )

    sources = [retriever.to_source(hit) for hit in hits]
    context = "\n\n".join(hit["text"] for hit in hits)

    content_parts, used_images, dropped = parts_builder.build_parts(
        question,
        normalized,
        sources,
        context,
        max_images=settings.max_vision_images,
    )

    llm_started = time.perf_counter()
    if used_images:
        answer, usage = llm.generate_multimodal(question, context, content_parts)
    else:
        answer, usage = llm.generate_answer(question, context)
    llm_ms = round((time.perf_counter() - llm_started) * 1000, 1)

    timings = {
        "retrieval_ms": retrieval_ms,
        "image_ms": image_ms,
        "llm_ms": llm_ms,
    }
    timings.update({key: float(value) for key, value in usage.items()})

    logger.info(
        "chat answered: kb=%s sources=%s user_images=%s used_images=%s",
        request.knowledge_base_id,
        len(sources),
        len(normalized),
        len(used_images),
    )
    return ChatResponse(
        answer=answer,
        sources=sources,
        used_images=used_images,
        degraded={"dropped_images": dropped},
        timings=timings,
        trace_id=trace_id,
    )
