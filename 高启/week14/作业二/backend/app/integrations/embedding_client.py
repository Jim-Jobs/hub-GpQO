"""调用本机 vLLM OpenAI 兼容接口生成文本向量。"""

from typing import Any

from backend.app.integrations.errors import EmbeddingError


class OpenAIEmbeddingClient:
    """以 OpenAI Python SDK 调用 BGE-M3 Embeddings 服务。"""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        client: Any | None = None,
        batch_size: int = 32,
    ) -> None:
        """保存模型配置；SDK 客户端可注入以便替身测试。"""
        if not base_url:
            raise ValueError("缺少 embedding 服务地址")
        if batch_size < 1:
            raise ValueError("embedding 批大小必须为正数")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise EmbeddingError("缺少 OpenAI Python SDK，请安装后端依赖") from error
            client = OpenAI(base_url=base_url, api_key="EMPTY")
        self._client = client
        self._model = model
        self._batch_size = batch_size

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """按顺序分批请求向量，并按 API 返回的 index 恢复输入顺序。"""
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), self._batch_size):
                batch = texts[start : start + self._batch_size]
                response = self._client.embeddings.create(model=self._model, input=batch)
                ordered = sorted(response.data, key=lambda item: item.index)
                vectors.extend([list(item.embedding) for item in ordered])
        except Exception as error:
            raise EmbeddingError("vLLM BGE-M3 embedding 请求失败") from error
        return vectors
