"""让 Qwen3-VL 独立判断检索文本是否足以回答问题。"""

import json

from backend.app.agents.chat.models import ChatModelClient, EvidenceAssessment, RetrievedChunk
from backend.app.integrations.errors import ChatModelError


def evaluate_evidence(
    question_text: str,
    chunks: list[RetrievedChunk],
    model: ChatModelClient,
    prompt: str,
) -> EvidenceAssessment:
    """只用用户文字和 PDF 文本证据判断充分性；空结果直接视为不足。"""
    if not chunks:
        return EvidenceAssessment(False, "资料库没有检索到相关 PDF 文本证据。")
    evidence = [
        {
            "chunk_id": item.chunk.chunk_id,
            "document_id": item.chunk.document_id,
            "page_number": item.chunk.page_number,
            "text": item.chunk.text,
        }
        for item in chunks
    ]
    response = model.complete(
        prompt,
        json.dumps({"question": question_text, "evidence": evidence}, ensure_ascii=False),
    )
    try:
        values = json.loads(response)
    except (TypeError, json.JSONDecodeError) as error:
        raise ChatModelError("证据判断响应不是有效 JSON") from error
    if not isinstance(values, dict) or not isinstance(values.get("sufficient"), bool):
        raise ChatModelError("证据判断响应缺少布尔 sufficient 字段")
    reason = values.get("reason", "")
    if not isinstance(reason, str):
        raise ChatModelError("证据判断 reason 必须是字符串")
    return EvidenceAssessment(values["sufficient"], reason.strip())
