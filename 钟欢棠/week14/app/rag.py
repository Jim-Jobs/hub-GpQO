"""轻量检索层：Markdown 分块 + 向量/词法混合检索 + 本地 JSON 索引。

向量优先使用 OpenAI 兼容的 embeddings 接口（如 text-embedding-v3）；
没有 API Key 时自动退化为中文 bigram 词法检索，保证离线也能跑通 RAG 流程。
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections import Counter
from pathlib import Path

import numpy as np

from .config import settings

logger = logging.getLogger("mmrag.rag")

IMAGE_URL_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
PAGE_HEAD_RE = re.compile(r"^#{1,4}\s*第\s*(\d+)\s*页", re.MULTILINE)
TOKEN_RE = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

_write_lock = threading.Lock()


def _index_path() -> Path:
    return settings.data_dir / "index" / "chunks.json"


def load_index() -> dict:
    path = _index_path()
    if not path.exists():
        return {"chunks": [], "dim": 0}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("chunks"), list):
            return data
    except (ValueError, OSError) as exc:
        logger.warning("索引读取失败：%s", exc)
    return {"chunks": [], "dim": 0}


def save_index(index: dict) -> None:
    path = _index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _images_in(text: str) -> list[str]:
    urls: list[str] = []
    for url in IMAGE_URL_RE.findall(text):
        if url.startswith(settings.media_prefix) or url.startswith(("http://", "https://")):
            if url not in urls:
                urls.append(url)
    return urls


def build_chunks(markdown: str, doc_id: str, filename: str) -> list[dict]:
    """按字符长度滑窗切块，保留页码与块内图片引用。"""
    size = max(200, settings.chunk_size)
    overlap = max(0, min(settings.chunk_overlap, size // 2))
    buf = ""
    page = 0
    segments: list[tuple[str, int]] = []
    for line in markdown.splitlines(keepends=True):
        matched = PAGE_HEAD_RE.match(line)
        if matched:
            page = int(matched.group(1))
        if buf.strip() and len(buf) + len(line) > size:
            segments.append((buf.strip(), page))
            buf = buf[-overlap:] + line if overlap else line
        else:
            buf += line
    if buf.strip():
        segments.append((buf.strip(), page))

    chunks = []
    for seq, (text, page_no) in enumerate(segments):
        chunks.append(
            {
                "id": f"{doc_id}:{seq}",
                "doc_id": doc_id,
                "filename": filename,
                "seq": seq,
                "page": page_no,
                "text": text,
                "images": _images_in(text),
            }
        )
    return chunks


def add_document(doc_id: str, chunks: list[dict]) -> int:
    """把一份文档的块写入索引（同 doc_id 重复解析会替换旧数据）。"""
    from . import llm  # 延迟导入，避免无 openai 依赖时影响其它模块

    embeddings = None
    if settings.embed_enabled and settings.llm_configured:
        embeddings = llm.embed([c["text"] for c in chunks])
    if embeddings and len(embeddings) == len(chunks) and embeddings[0]:
        dim = len(embeddings[0])
    else:
        dim = 0
        for chunk in chunks:
            chunk.pop("embedding", None)

    with _write_lock:
        index = load_index()
        kept = [c for c in index["chunks"] if c.get("doc_id") != doc_id]
        if dim:
            for chunk, vector in zip(chunks, embeddings):
                chunk["embedding"] = vector
        kept.extend(chunks)
        index = {"chunks": kept, "dim": dim or index.get("dim", 0)}
        save_index(index)
    logger.info("索引已更新：doc=%s 块数=%s 向量维度=%s", doc_id, len(chunks), dim)
    return len(chunks)


def _tokens(text: str) -> Counter:
    lowered = text.lower()
    words = TOKEN_RE.findall(lowered)
    cjk = CJK_RE.findall(lowered)
    bigrams = [a + b for a, b in zip(cjk, cjk[1:])]
    return Counter(words + bigrams)


def keyword_score(query: str, text: str) -> float:
    q_tokens = _tokens(query)
    t_tokens = _tokens(text[:4000])
    if not q_tokens:
        return 0.0
    overlap = sum(min(count, t_tokens.get(token, 0)) * (1.5 if len(token) > 1 else 1.0) for token, count in q_tokens.items())
    return overlap / (sum(q_tokens.values()) + 1e-6)


def retrieve(query: str, top_k: int | None = None, doc_ids: list[str] | None = None) -> list[tuple[dict, float]]:
    """返回 [(chunk, score)]，按相关度降序。"""
    top_k = top_k or settings.top_k
    index = load_index()
    pool = [c for c in index["chunks"] if not doc_ids or c.get("doc_id") in doc_ids]
    if not pool:
        return []

    lexical = np.array([keyword_score(query, c.get("text", "")) for c in pool], dtype=np.float32)
    lexical_norm = lexical / (lexical.max() + 1e-6) if lexical.size else lexical

    vector_scores = None
    if settings.embed_enabled and settings.llm_configured and index.get("dim"):
        from . import llm

        query_vector = llm.embed([query])
        vectors = [c.get("embedding") for c in pool]
        if query_vector and all(v and len(v) == len(query_vector[0]) for v in vectors):
            matrix = np.array(vectors, dtype=np.float32)
            q = np.array(query_vector[0], dtype=np.float32)
            matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-8
            q /= np.linalg.norm(q) + 1e-8
            cosine = matrix @ q
            vector_scores = (cosine - cosine.min()) / (cosine.max() - cosine.min() + 1e-8)
        else:
            logger.info("部分块缺少向量或查询向量不可用，改用词法检索")
    if vector_scores is not None:
        scores = 0.75 * vector_scores + 0.25 * lexical_norm
    else:
        scores = lexical_norm

    order = np.argsort(-scores)[:top_k]
    return [(pool[int(i)], float(scores[int(i)])) for i in order if scores[int(i)] > 0]


def index_stats() -> dict:
    index = load_index()
    docs = {c.get("doc_id") for c in index["chunks"]}
    return {"chunks": len(index["chunks"]), "documents": len(docs), "dim": index.get("dim", 0)}
