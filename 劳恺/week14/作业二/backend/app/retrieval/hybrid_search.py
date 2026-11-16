"""三路混合检索 + RRF 融合。

对应文档：``docs/06-RAG与多模态设计.md` §3。

| 路 | 查询对象 | 擅长 |
|---|---|---|
| ① BM25 | ``chunks.tokens``（jieba 预分词） | 专有名词、数字、术语、罕见词 |
| ② 向量 | ``chunks.vector`` vs query_vector | 语义相近、改写、不同表述 |
| ③ 图片向量 | ``images.vector`` vs query_vector | 问题语义只体现在图里 |

**为什么自己实现 RRF 而不是用 ES 内置的**：ES 的 ``retriever`` RRF 要求 8.14+，
而且难以插入"图片路"这种自定义检索。自己在 Python 层融合，三路可独立替换、可单测。

**RRF 公式**：``score(d) = Σ 1/(k + rank_r(d))``，``k = 60``，rank 从 1 起。
各路先各取 top-20，融合后取 top-8。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.config import Settings
from app.llm.embedding_client import EmbeddingClient
from app.retrieval.jieba_tokenize import tokenize
from app.storage.es_client import ESClient
from app.storage.index_schema import INDEX_CHUNKS, INDEX_IMAGES

logger = logging.getLogger(__name__)

ROUTE_BM25 = "bm25"
ROUTE_VECTOR = "vector"
ROUTE_IMAGE = "image"


@dataclass
class RetrievedImage:
    """检索到的图片。"""

    image_id: str
    doc_id: str = ""
    doc_filename: str = ""
    chunk_id: str = ""
    page_no: int = 0
    url: str = ""
    caption: str = ""
    path: str = ""


@dataclass
class RetrievedChunk:
    """融合后的检索块。"""

    chunk_id: str
    doc_id: str = ""
    doc_filename: str = ""
    text: str = ""
    tokens: str = ""
    page_start: int = 0
    page_end: int = 0
    score: float = 0.0
    """RRF 融合分数。"""
    routes: list[str] = field(default_factory=list)
    """命中了哪几路（用于评测分析）。"""
    images: list[RetrievedImage] = field(default_factory=list)
    rank_in_route: dict[str, int] = field(default_factory=dict)
    """每路的排名（从 1 起）。"""


def rrf_fuse(
    routes: dict[str, list[tuple[str, dict]]], *, k: int = 60
) -> list[tuple[str, float, dict[str, int], set[str]]]:
    """RRF 融合。

    ``routes`` 是 ``{路名: [(chunk_id, 附加信息), ...]}``，每路已按相关性降序。

    返回 ``[(chunk_id, 分数, {路名: 排名}, 命中路集合)]``，按分数降序。
    """
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    hit_routes: dict[str, set[str]] = {}

    for route_name, ranked in routes.items():
        for rank, (chunk_id, _info) in enumerate(ranked, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
            ranks.setdefault(chunk_id, {})[route_name] = rank
            hit_routes.setdefault(chunk_id, set()).add(route_name)

    fused = [
        (chunk_id, score, ranks[chunk_id], hit_routes[chunk_id])
        for chunk_id, score in scores.items()
    ]
    fused.sort(key=lambda item: (-item[1], item[0]))
    return fused


class HybridSearcher:
    """三路检索器。"""

    def __init__(
        self,
        es: ESClient,
        embedder: EmbeddingClient,
        settings: Settings,
    ) -> None:
        self._es = es
        self._embedder = embedder
        self._settings = settings

    async def search(
        self,
        question: str,
        *,
        doc_ids: list[str] | None = None,
        top_k_final: int | None = None,
    ) -> list[RetrievedChunk]:
        """执行三路检索并融合。"""
        settings = self._settings
        top_k_final = top_k_final or settings.top_k_final

        bm25_hits = await self._search_bm25(question, doc_ids)
        vector_hits = await self._search_vector(question, doc_ids)
        image_hits = await self._search_image_vector(question, doc_ids)

        routes = {
            ROUTE_BM25: bm25_hits,
            ROUTE_VECTOR: vector_hits,
            ROUTE_IMAGE: image_hits,
        }
        fused = rrf_fuse(routes, k=settings.rrf_rank_constant)

        # 取出被融合命中的块，按融合分数排序
        wanted = {chunk_id for chunk_id, _score, _ranks, _routes in fused}
        details = await self._fetch_chunks(wanted, doc_ids)
        image_by_chunk = await self._fetch_images(image_hits)

        results: list[RetrievedChunk] = []
        for chunk_id, score, ranks, hit_routes in fused:
            detail = details.get(chunk_id)
            if detail is None:
                continue
            chunk = RetrievedChunk(
                chunk_id=chunk_id,
                doc_id=detail.get("doc_id", ""),
                doc_filename=detail.get("doc_filename", ""),
                text=detail.get("text", ""),
                tokens=detail.get("tokens", ""),
                page_start=detail.get("page_start", 0),
                page_end=detail.get("page_end", 0),
                score=score,
                routes=sorted(hit_routes),
                rank_in_route=ranks,
                images=image_by_chunk.get(chunk_id, []),
            )
            results.append(chunk)
            if len(results) >= top_k_final:
                break

        logger.info(
            "混合检索完成: bm25=%d vector=%d image=%d → 融合 %d",
            len(bm25_hits),
            len(vector_hits),
            len(image_hits),
            len(results),
        )
        return results

    # ---------- 三路 ----------

    async def _search_bm25(
        self, question: str, doc_ids: list[str] | None
    ) -> list[tuple[str, dict]]:
        """BM25：查 ``chunks.tokens``。

        **查询也要 jieba 预分词** —— ``tokens`` 字段用的是 whitespace 分析器，
        原始中文会被当成一整个 term，与索引里的词对不上。
        """
        query_text = tokenize(question)
        if not query_text:
            return []
        body: dict = {
            "query": {"match": {"tokens": query_text}},
            "size": self._settings.top_k_bm25,
            "_source": False,
        }
        if doc_ids:
            body["query"] = {
                "bool": {
                    "must": [{"match": {"tokens": query_text}}],
                    "filter": [{"terms": {"doc_id": doc_ids}}],
                }
            }
        result = await self._es.search(INDEX_CHUNKS, body)
        return [
            (hit["_id"], hit.get("_score", 0.0))
            for hit in result["hits"]["hits"]
        ]

    async def _search_vector(
        self, question: str, doc_ids: list[str] | None
    ) -> list[tuple[str, dict]]:
        """向量路：``chunks.vector`` 上的 kNN。"""
        vectors = await self._embedder.embed_texts([question])
        if not vectors or not vectors[0]:
            return []
        query_vector = vectors[0]

        knn: dict = {
            "field": "vector",
            "query_vector": query_vector,
            "k": self._settings.top_k_vector,
            "num_candidates": max(100, self._settings.top_k_vector * 10),
        }
        if doc_ids:
            knn["filter"] = {"terms": {"doc_id": doc_ids}}
        body = {"knn": knn, "size": self._settings.top_k_vector, "_source": False}
        result = await self._es.search(INDEX_CHUNKS, body)
        return [
            (hit["_id"], hit.get("_score", 0.0))
            for hit in result["hits"]["hits"]
        ]

    async def _search_image_vector(
        self, question: str, doc_ids: list[str] | None
    ) -> list[tuple[str, dict]]:
        """图片向量路：``images.vector`` 上的 kNN，再换算成 chunk_id。

        命中的是图片，但参与融合的必须是**文字块**（图片不单独成块）。
        因此这里的 chunk_id 来自图片绑定的 ``chunk_id`` 字段。
        """
        vectors = await self._embedder.embed_texts([question])
        if not vectors or not vectors[0]:
            return []
        query_vector = vectors[0]

        knn: dict = {
            "field": "vector",
            "query_vector": query_vector,
            "k": self._settings.top_k_vector,
            "num_candidates": max(100, self._settings.top_k_vector * 10),
        }
        if doc_ids:
            knn["filter"] = {"terms": {"doc_id": doc_ids}}
        body = {"knn": knn, "size": self._settings.top_k_vector}
        result = await self._es.search(INDEX_IMAGES, body)

        # 图片排名 → chunk 排名（取该 chunk 上排名最高的图片）
        best: dict[str, int] = {}
        chunk_of: dict[str, str] = {}
        for rank, hit in enumerate(result["hits"]["hits"], start=1):
            chunk_id = hit["_source"].get("chunk_id")
            if not chunk_id:
                continue
            chunk_of[chunk_id] = hit["_id"]
            if chunk_id not in best:
                best[chunk_id] = rank
        return [(chunk_id, {"rank": best[chunk_id]}) for chunk_id in best]

    # ---------- 取详情 ----------

    async def _fetch_chunks(
        self, chunk_ids: set[str], doc_ids: list[str] | None
    ) -> dict[str, dict]:
        if not chunk_ids:
            return {}
        body = {
            "query": {"terms": {"chunk_id": list(chunk_ids)}},
            "size": len(chunk_ids),
        }
        if doc_ids:
            body["query"] = {
                "bool": {
                    "must": [{"terms": {"chunk_id": list(chunk_ids)}}],
                    "filter": [{"terms": {"doc_id": doc_ids}}],
                }
            }
        result = await self._es.search(INDEX_CHUNKS, body)
        return {hit["_id"]: hit["_source"] for hit in result["hits"]["hits"]}

    async def _fetch_images(
        self, image_hits: list[tuple[str, dict]]
    ) -> dict[str, list[RetrievedImage]]:
        """把图片路命中的图片挂到对应 chunk 上。

        优先用**块内嵌的 images 字段**（那里有 caption 与 url），
        图片索引里的记录作为补充。
        """
        if not image_hits:
            return {}
        chunk_ids = [chunk_id for chunk_id, _info in image_hits]
        result = await self._es.search(
            INDEX_CHUNKS,
            {
                "query": {"terms": {"chunk_id": chunk_ids}},
                "size": len(chunk_ids),
                "_source": ["chunk_id", "images", "doc_id", "doc_filename"],
            },
        )
        by_chunk: dict[str, list[RetrievedImage]] = {}
        for hit in result["hits"]["hits"]:
            source = hit["_source"]
            chunk_id = hit["_id"]
            images: list[RetrievedImage] = []
            for item in source.get("images", []):
                images.append(
                    RetrievedImage(
                        image_id=item.get("image_id", ""),
                        doc_id=item.get("doc_id", source.get("doc_id", "")),
                        doc_filename=source.get("doc_filename", ""),
                        chunk_id=chunk_id,
                        page_no=item.get("page_no", 0),
                        url=item.get("url", ""),
                        caption=item.get("caption", ""),
                        path=item.get("path", ""),
                    )
                )
            if images:
                by_chunk[chunk_id] = images
        return by_chunk
