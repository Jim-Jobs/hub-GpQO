"""VL 多模态消息组装（纯函数，便于单测）。

OpenAI 兼容格式：content 数组混合 {"type":"text"} 与 {"type":"image_url"}。
用户图直接送模型（真·看图）；命中资料里的图片也以 base64 送模型辅助理解。
"""

import base64
from pathlib import Path

from mrag.config import PROCESSED_DIR, get_settings

SYSTEM_PROMPT = "你是一个基于文档库回答问题的多模态助手。回答用中文，markdown 格式。"

OUTPUT_REQUIREMENTS = """回答要求：
- 基于提供的资料回答，客观、有逻辑；资料为空时明确说明当前没有可参考的文档，再凭常识回答并注明。
- 资料中包含图片链接（/files/... 开头）时，把相关图片链接原样保留在回答的合适位置，单独成行，不要修改路径。
- 用户附带了图片时，先描述你对图片内容的理解再作答。"""


def files_url_to_local(url: str) -> Path | None:
    """/files/processed/... -> 本地绝对路径。"""
    if not url.startswith("/files/"):
        return None
    local = PROCESSED_DIR.parent / url[len("/files/") :]
    return local if local.is_file() else None


def read_image_as_data_url(local_path: str | Path) -> str | None:
    """本地图片 -> data:image/...;base64 URL；失败返回 None。"""
    try:
        raw = Path(local_path).read_bytes()
    except OSError:
        return None
    suffix = Path(local_path).suffix.lower().lstrip(".") or "png"
    mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(suffix, "jpeg")
    return f"data:image/{mime};base64," + base64.b64encode(raw).decode()


def build_vl_messages(
    history: list[dict],
    question: str,
    context_blocks: list[dict],
    user_image_b64: str | None = None,
    context_image_urls: list[str] | None = None,
) -> list[dict]:
    """组装 messages。

    history: [{"role","content"}]（纯文本，前端持有）
    context_blocks: [{"file_name","text"}] 检索命中文块（text 内图片已是 /files/ URL）
    user_image_b64: 用户上传图（裸 base64，无 data: 前缀）
    context_image_urls: 命中资料中的图片 URL（限制数量送模型）
    """
    s = get_settings()
    parts_text: list[str] = []
    if context_blocks:
        refs = "\n\n".join(f"【资料·{b['file_name']}】\n{b['text']}" for b in context_blocks)
        parts_text.append(f"相关资料：\n{refs}")
    else:
        parts_text.append("相关资料：（文档库中暂无命中内容）")
    parts_text.append(f"用户的问题：{question}")
    parts_text.append(OUTPUT_REQUIREMENTS)

    content: list[dict] = [{"type": "text", "text": "\n\n".join(parts_text)}]

    if user_image_b64:
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{user_image_b64}"}}
        )

    sent = 0
    for url in context_image_urls or []:
        if sent >= s.vl_max_images:
            break
        local = files_url_to_local(url)
        if local is None:
            continue
        data_url = read_image_as_data_url(local)
        if data_url:
            content.append({"type": "image_url", "image_url": {"url": data_url}})
            sent += 1

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history[-10:]:  # 最多带 10 轮历史
        role = h.get("role", "user")
        text = str(h.get("content", ""))[:4000]
        if role in ("user", "assistant") and text:
            messages.append({"role": role, "content": text})
    messages.append({"role": "user", "content": content})
    return messages
