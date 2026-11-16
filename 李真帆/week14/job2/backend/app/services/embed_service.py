import logging
import time

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)

BATCH_SIZE = 32
RETRIES = 3


class ApiEmbedding:
    """OpenAI 兼容 /embeddings，默认指向 vLLM 自部署服务。"""

    def __init__(self):
        from openai import OpenAI

        self._client = OpenAI(base_url=settings.embed_base_url, api_key=settings.embed_api_key)
        self._model = settings.embedding_model
        self.dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i : i + BATCH_SIZE]
            resp = None
            for attempt in range(RETRIES):
                try:
                    resp = self._client.embeddings.create(model=self._model, input=batch)
                    break
                except Exception:
                    if attempt == RETRIES - 1:
                        raise
                    time.sleep(2 * (attempt + 1))
            data = sorted(resp.data, key=lambda d: d.index)
            vecs = [d.embedding for d in data]
            self.dim = len(vecs[0])
            out.extend(vecs)
        return out


class LocalEmbedding:
    """fastembed 本地兜底（EMBEDDING_PROVIDER=local）。"""

    def __init__(self):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=settings.local_embed_model)
        self.dim = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        vecs = [[float(x) for x in v] for v in self._model.embed(texts)]
        if vecs:
            self.dim = len(vecs[0])
        return vecs


_provider = None


def get_provider():
    global _provider
    if _provider is None:
        if settings.embedding_provider == "local":
            _provider = LocalEmbedding()
        else:
            _provider = ApiEmbedding()
        logger.info("embedding provider ready: %s", type(_provider).__name__)
    return _provider


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return get_provider().embed(texts)


def vec_to_blob(vec: list[float]) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def blob_to_vec(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
