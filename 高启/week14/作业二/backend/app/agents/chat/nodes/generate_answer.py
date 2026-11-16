"""根据已审查证据生成带验证引用和受控图片的回答。"""

import json

from backend.app.agents.chat.models import (
    ChatModelClient,
    ImageLoader,
    RetrievedChunk,
    RetrievedImage,
)
from backend.app.chat.domain.models import ChatImage, ChatResponse
from backend.app.integrations.errors import ChatModelError


MAX_PDF_IMAGES = 3


def generate_answer(
    question_text: str,
    user_image: ChatImage | None,
    chunks: list[RetrievedChunk],
    model: ChatModelClient,
    prompt: str,
    image_loader: ImageLoader | None = None,
) -> ChatResponse:
    """传入证据和最多三张去重 PDF 图片，并将模型来源限制到已检索 ID。"""
    pdf_images = _load_pdf_images(chunks, image_loader)
    evidence = [
        {
            "chunk_id": item.chunk.chunk_id,
            "document_id": item.chunk.document_id,
            "file_name": item.file_name,
            "page_number": item.chunk.page_number,
            "text": item.chunk.text,
        }
        for item in chunks
    ]
    available_images = [
        {
            "asset_id": image.asset_id,
            "document_id": image.document_id,
            "page_number": image.page_number,
        }
        for image in pdf_images
    ]
    user_prompt = json.dumps(
        {
            "question": question_text,
            "evidence": evidence,
            "available_pdf_images": available_images,
        },
        ensure_ascii=False,
    )
    model_images = ([user_image] if user_image else []) + [item.image for item in pdf_images]
    raw_response = model.complete(prompt, user_prompt, model_images)
    return _build_response(raw_response, chunks, pdf_images)


def _load_pdf_images(
    chunks: list[RetrievedChunk], image_loader: ImageLoader | None
) -> list[RetrievedImage]:
    """按检索排序遍历图片 ID，校验后最多加载三张不同资源。"""
    if image_loader is None:
        return []
    images: list[RetrievedImage] = []
    seen: set[tuple[str, str]] = set()
    for item in chunks:
        for asset_id in item.chunk.asset_ids:
            key = (item.chunk.document_id, asset_id)
            if key in seen:
                continue
            seen.add(key)
            image = image_loader(*key)
            if image is None:
                continue
            images.append(
                RetrievedImage(
                    item.chunk.document_id,
                    asset_id,
                    item.chunk.page_number,
                    item.file_name,
                    image,
                )
            )
            if len(images) == MAX_PDF_IMAGES:
                return images
    return images


def _build_response(
    raw_response: str,
    chunks: list[RetrievedChunk],
    images: list[RetrievedImage],
) -> ChatResponse:
    """只输出检索结果中存在的 chunk 和已加载图片，拒绝无来源答案。"""
    try:
        values = json.loads(raw_response)
    except (TypeError, json.JSONDecodeError) as error:
        raise ChatModelError("答案生成响应不是有效 JSON") from error
    if not isinstance(values, dict) or not isinstance(values.get("segments"), list):
        raise ChatModelError("答案生成响应缺少 segments 数组")

    chunks_by_id = {item.chunk.chunk_id: item for item in chunks}
    images_by_id = {item.asset_id: item for item in images}
    citations = _validated_citations(values.get("citation_chunk_ids"), chunks_by_id)
    if not citations:
        raise ChatModelError("答案没有引用任何本次检索到的 chunk")

    answer_segments: list[dict] = []
    image_assets: dict[str, dict] = {}
    for segment in values["segments"]:
        if not isinstance(segment, dict):
            continue
        if segment.get("type") == "text" and isinstance(segment.get("text"), str):
            text = segment["text"].strip()
            if text:
                answer_segments.append({"type": "text", "text": text})
        elif segment.get("type") == "image":
            image = images_by_id.get(segment.get("asset_id"))
            if image is None:
                continue
            asset = {
                "document_id": image.document_id,
                "asset_id": image.asset_id,
                "file_name": image.file_name,
                "page_number": image.page_number,
            }
            image_assets[image.asset_id] = asset
            answer_segments.append({"type": "image", **asset})
    if not any(segment["type"] == "text" for segment in answer_segments):
        raise ChatModelError("答案没有有效文字内容")
    return ChatResponse(
        route="in_scope",
        answer_segments=answer_segments,
        citations=citations,
        image_assets=list(image_assets.values()),
    )


def _validated_citations(
    value: object, chunks_by_id: dict[str, RetrievedChunk]
) -> list[dict]:
    """按 chunk_id 去重并丢弃模型捏造或未检索的来源。"""
    if not isinstance(value, list):
        return []
    citations: list[dict] = []
    seen: set[str] = set()
    for chunk_id in value:
        if not isinstance(chunk_id, str) or chunk_id in seen:
            continue
        retrieved = chunks_by_id.get(chunk_id)
        if retrieved is None:
            continue
        seen.add(chunk_id)
        citations.append(
            {
                "chunk_id": chunk_id,
                "document_id": retrieved.chunk.document_id,
                "file_name": retrieved.file_name,
                "page_number": retrieved.chunk.page_number,
            }
        )
    return citations
