"""检索：bge 文本 top-k ＋ 图问时 CLIP 图像 top-k，去重合并。"""

import re

from mrag import zilliz_store
from mrag.config import get_settings
from mrag.embeddings import encode_image_b64, encode_text_bge
from mrag.zilliz_store import CLIP_IMAGE_FIELD, TEXT_FIELD

FILES_URL_RE = re.compile(r"/files/[^\s)]+")


def search_context(question: str, image_b64: str | None = None) -> dict:
    """返回 {"blocks": [{file_name,text,score}], "image_urls": [命中资料中的 /files/ 图片]}"""
    s = get_settings()
    blocks: list[dict] = []
    seen: set[str] = set()

    def _merge(hits: list[dict]) -> None:
        for h in hits:
            text = h.get("text") or ""
            key = text[:100]
            if not text or key in seen:
                continue
            seen.add(key)
            blocks.append({"file_name": h.get("file_name") or "?", "text": text, "score": h.get("score")})

    text_hits = zilliz_store.search(encode_text_bge(question), TEXT_FIELD, s.text_top_k)
    _merge(text_hits)

    if image_b64:
        img_vec = encode_image_b64(image_b64)
        if img_vec:
            img_hits = zilliz_store.search(img_vec, CLIP_IMAGE_FIELD, s.image_top_k)
            _merge(img_hits)

    # 收集命中资料中的图片 URL（去重、保序）
    urls: list[str] = []
    for b in blocks:
        for u in FILES_URL_RE.findall(b["text"]):
            if u not in urls:
                urls.append(u)
    return {"blocks": blocks, "image_urls": urls}
