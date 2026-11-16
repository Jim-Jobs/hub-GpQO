import json
import logging

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    return _client


def ensure_api_key() -> None:
    if not settings.deepseek_api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，请在 backend/.env 填写")


def judge_relevance(question: str, candidates: list[dict]) -> tuple[bool, str]:
    """candidates: [{doc_name, heading, preview}]，返回 (related, reason)。"""
    parts = []
    for i, c in enumerate(candidates, 1):
        parts.append(f"[{i}] 文档《{c['doc_name']}》 {c['heading']}\n{c['preview']}")
    prompt = (
        "以下是知识库检索出的候选片段，请判断它们是否包含回答用户问题所需的信息。\n\n"
        f"用户问题：{question}\n\n"
        "候选片段：\n" + "\n\n".join(parts) + "\n\n"
        '只输出 JSON：{"related": true 或 false, "reason": "一句话理由"}。'
        "仅当片段确实包含可用于回答问题的实质信息时 related 才为 true；仅主题相近但无答案视为 false。"
    )
    resp = _get_client().chat.completions.create(
        model=settings.chat_model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=300,
    )
    text = (resp.choices[0].message.content or "").strip()
    try:
        data = json.loads(text)
        return bool(data.get("related")), str(data.get("reason", ""))
    except json.JSONDecodeError:
        logger.warning("相关性判断输出非 JSON: %s", text[:200])
        return False, "判断输出解析失败"


def stream_answer(messages: list[dict], model: str):
    """流式生成，逐段 yield 增量文本。"""
    ensure_api_key()
    stream = _get_client().chat.completions.create(
        model=model, messages=messages, stream=True, temperature=0.3,
        max_tokens=settings.chat_max_tokens,
    )
    for chunk in stream:
        if chunk.choices:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta


def image_content(text: str, image_data_uri: str) -> list[dict]:
    """OpenAI 兼容的多模态 content 数组。"""
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": image_data_uri}},
    ]
