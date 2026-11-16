"""大模型调用：embeddings + 多模态 chat completions（OpenAI 兼容协议）。

支持两种多模态能力：
  1. 知识库检索到的图片 + 文本上下文 -> LLM，回答中输出 Markdown 图片（图文混排）；
  2. 用户上传的图片 + 问题 + 检索上下文，一起作为一条 multimodal user message 送入 LLM。
未配置 API Key / 调用失败时进入「演示模式」，明确标注返回的是检索摘录而非模型回答。
"""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
from pathlib import Path

from .config import settings

logger = logging.getLogger("mmrag.llm")

_client = None

SYSTEM_PROMPT = """你是一个多模态文档问答助手，基于知识库中检索到的 PDF 片段与图片回答问题。

规则：
1. 只依据【检索片段】与【图片】作答，关键结论后用 [编号] 标注来源片段；资料不足时明确说明。
2. 回答要图文混排：当【可用图片】中存在与问题相关的图片时，用 Markdown 语法插入 1-3 张图片，
   格式严格为 ![图片说明](图片URL)。只能使用下面列出的 URL，禁止编造图片地址，并在图片前后配文字说明。
3. 如果用户随问题上传了图片，必须结合图片内容一起理解并回答。
4. 使用简体中文、Markdown 排版（小标题、要点列表），保持简洁准确。
"""


def get_client():
    global _client
    if not settings.llm_configured:
        return None
    if _client is None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError("缺少 openai 依赖，请执行：pip install -r requirements.txt") from exc
        _client = OpenAI(api_key=settings.llm_api_key, base_url=settings.llm_base_url, timeout=180, max_retries=1)
    return _client


def embed(texts: list[str]) -> list[list[float]] | None:
    client = get_client()
    if client is None or not settings.embed_enabled or not texts:
        return None
    try:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 8):  # 兼容 DashScope 的批量上限
            response = client.embeddings.create(model=settings.embed_model, input=texts[start:start + 8])
            vectors.extend(item.embedding for item in response.data)
        return vectors if len(vectors) == len(texts) else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("向量化失败，将使用词法检索：%s", exc)
        return None


def image_to_data_url(path: str | Path, max_side: int = 1024) -> str | None:
    """把本地图片压缩成 base64 data URL，供视觉模型读取。"""
    source = Path(path)
    if not source.is_file():
        return None
    data = source.read_bytes()
    mime = mimetypes.guess_type(source.name)[0] or "image/jpeg"
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image.thumbnail((max_side, max_side))
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=82)
        data = buffer.getvalue()
        mime = "image/jpeg"
    except Exception as exc:  # noqa: BLE001
        logger.debug("图片压缩跳过：%s", exc)
    if len(data) > 8 * 1024 * 1024:
        logger.warning("图片过大，跳过：%s", source)
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _call(messages: list[dict], model: str) -> str:
    client = get_client()
    if client is None:
        raise RuntimeError("未配置 LLM_API_KEY")
    response = client.chat.completions.create(model=model, messages=messages, temperature=0.3, max_tokens=1800)
    return (response.choices[0].message.content or "").strip()


def _history_text(history: list[dict]) -> str:
    lines = []
    for item in history[-6:]:
        role = "用户" if item.get("role") == "user" else "助手"
        lines.append(f"- {role}: {str(item.get('content', ''))[:300]}")
    return "\n".join(lines) or "（无历史对话）"


def _demo_answer(question: str, ranked: list[tuple[dict, float]], image_urls: list[str]) -> str:
    parts = [
        "> 【演示模式】未配置 `LLM_API_KEY` 或大模型调用失败，"
        "下面展示的是检索结果摘录，不是模型生成的回答。配置 Key 后即可获得真正的图文混排回答。",
        "",
        f"**问题**：{question}",
        "",
    ]
    for index, (chunk, score) in enumerate(ranked[:3], start=1):
        parts.append(f"**[{index}] {chunk.get('filename', '文档')} 第 {chunk.get('page') or '?'} 页（相关度 {score:.2f}）**")
        parts.append("")
        parts.append("> " + chunk.get("text", "")[:400].replace("\n", "\n> "))
        parts.append("")
    for index, url in enumerate(image_urls[:2], start=1):
        parts.append(f"![检索到的相关图片 {index}]({url})")
        parts.append("")
    if not ranked:
        parts.append("知识库中还没有可用内容，请先上传并等待 PDF 解析完成。")
    return "\n".join(parts)


def answer(
    question: str,
    ranked: list[tuple[dict, float]],
    doc_names: dict[str, str] | None = None,
    user_image_paths: list[str] | None = None,
    history: list[dict] | None = None,
    use_vision: bool = True,
) -> dict:
    """执行一次多模态 RAG 问答，返回回答、来源和引用图片。"""
    doc_names = doc_names or {}
    history = history or []
    ranked = ranked[: settings.top_k]

    context_lines = []
    for index, (chunk, score) in enumerate(ranked, start=1):
        name = doc_names.get(chunk.get("doc_id"), chunk.get("filename", "文档"))
        page = chunk.get("page") or "?"
        context_lines.append(f"[{index}] 《{name}》第 {page} 页（相关度 {score:.2f}）：\n{chunk.get('text', '')[:1000]}")
    context = "\n\n".join(context_lines) or "（知识库暂无匹配片段）"

    image_urls: list[str] = []
    for chunk, _ in ranked:
        for url in chunk.get("images", []):
            if url not in image_urls:
                image_urls.append(url)
    if not use_vision:
        image_urls = []
    image_urls = image_urls[: max(0, settings.max_context_images) + 2]

    sources = [
        {
            "index": index,
            "doc_id": chunk.get("doc_id", ""),
            "filename": doc_names.get(chunk.get("doc_id"), chunk.get("filename", "文档")),
            "page": chunk.get("page", 0),
            "snippet": chunk.get("text", "")[:160],
        }
        for index, (chunk, _) in enumerate(ranked, start=1)
    ]

    user_images = []
    context_images = []
    if settings.vision_enabled:
        for path in (user_image_paths or []):
            data_url = image_to_data_url(path)
            if data_url:
                user_images.append(data_url)
        for url in image_urls[: settings.max_context_images]:
            data_url = image_to_data_url(settings.media_path(url))
            if data_url:
                context_images.append(data_url)

    user_text = (
        f"【用户问题】\n{question}\n\n"
        f"【历史对话】\n{_history_text(history)}\n\n"
        f"【检索片段】\n{context}\n\n"
        f"【可用图片 URL（回答中引用时原样使用）】\n" + ("\n".join(f"- {url}" for url in image_urls) or "（无）")
        + "\n\n请按系统规则给出图文混排的回答："
    )

    # qwen-vl 等视觉模型对「图片在前、文字在后」兼容性最好：
    # 前 N 张是用户上传图片，随后是知识库图片，最后一条文字给出全部上下文与图片说明。
    content: list[dict] = []
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in user_images[:2])
    content.extend({"type": "image_url", "image_url": {"url": url}} for url in context_images)
    if user_images or context_images:
        user_text += (
            f"\n\n【随消息附带的图片顺序】共 {len(user_images[:2]) + len(context_images)} 张："
            f"前 {len(user_images[:2])} 张是用户随问题上传的图片，"
            f"后面 {len(context_images)} 张是知识库检索到的相关图片。"
        )
    content.append({"type": "text", "text": user_text})

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]

    mode = "demo"
    text = ""
    client = get_client()
    if client is not None:
        try:
            if (user_images or context_images) and settings.vision_enabled:
                text = _call(messages, settings.llm_model)
                mode = "多模态 LLM"
            else:
                text = _call(
                    [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}],
                    settings.llm_model,
                )
                mode = "文本 LLM"
        except Exception as exc:  # noqa: BLE001
            logger.warning("多模态调用失败，降级为纯文本重试：%s", exc)
            try:
                text = _call(
                    [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}],
                    settings.llm_text_model,
                )
                mode = "文本 LLM（降级）"
            except Exception as exc2:  # noqa: BLE001
                logger.warning("纯文本调用也失败：%s", exc2)
    if not text:
        text = _demo_answer(question, ranked, image_urls)

    return {"answer": text, "mode": mode, "sources": sources, "images": image_urls}
