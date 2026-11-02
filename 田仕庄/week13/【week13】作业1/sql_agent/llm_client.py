"""轻量 OpenAI 兼容客户端（httpx + SSE 流式）。

为什么不用 langchain_openai：
    仅 `import langchain_openai` 就需约 4.2s（连带 azure/openai SDK），
    而本项目只用到 invoke / stream 两个能力。自实现后冷启动约省 2s，
    且能精确控制 thinking、超时与连接复用（同一个 Client 复用 TCP/TLS 连接）。

对外契约（与 langchain 的 BaseChatModel 保持最小兼容）：
    client.invoke(messages) -> 对象，含 .content
    client.stream(messages) -> 逐块产出对象，含 .content
    messages 形如 [("system", "..."), ("human", "...")]
"""

from __future__ import annotations

import json
import time
from typing import Iterable, Iterator

import httpx

from sql_agent.log import get_logger

logger = get_logger(__name__)

# LangChain 风格角色 → OpenAI 角色
_ROLE_MAP = {
    "human": "user",
    "ai": "assistant",
    "system": "system",
    "user": "user",
    "assistant": "assistant",
}


class LLMError(RuntimeError):
    """LLM 调用失败（网络/鉴权/协议错误统一包装）。"""


class ChatMessage:
    """最小消息对象（对齐 langchain 的 .content 约定）。"""

    __slots__ = ("content",)

    def __init__(self, content: str):
        self.content = content

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"ChatMessage({self.content[:40]!r})"


class ChatClient:
    """OpenAI 兼容接口客户端。

    参数：
        base_url:   形如 https://open.bigmodel.cn/api/paas/v4
        api_key:    Bearer Token
        model:      模型名
        extra_body: 附加请求参数（如 {"thinking": {"type": "disabled"}}）
        max_retries:失败重试次数（仅对非流式请求重试，流式中断不重试以免重复输出）
    """

    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.0, timeout: float = 60.0,
                 max_retries: int = 1, extra_body: dict | None = None,
                 max_tokens: int | None = None):
        self.model_name = model
        self.temperature = temperature
        self.max_retries = max_retries
        self.extra_body: dict = dict(extra_body or {})
        self.max_tokens = max_tokens

        self._base_url = base_url.rstrip("/") + "/"
        self._api_key = api_key
        self._timeout = timeout
        self._client: httpx.Client | None = None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    @property
    def http(self) -> httpx.Client:
        """懒创建并复用连接（省掉每次调用的 TCP/TLS 握手）。"""
        if self._client is None:
            self._client = httpx.Client(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    def _payload(self, messages: Iterable, stream: bool) -> dict:
        payload = {
            "model": self.model_name,
            "messages": [
                {"role": _ROLE_MAP.get(role, role), "content": content}
                for role, content in messages
            ],
            "temperature": self.temperature,
            "stream": stream,
        }
        if self.max_tokens:
            payload["max_tokens"] = self.max_tokens
        payload.update(self.extra_body)
        return payload

    @staticmethod
    def parse_sse_line(line: str) -> str | None:
        """解析一行 SSE，返回增量文本；无内容/结束标记返回 None。"""
        if not line.startswith("data:"):
            return None
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            return None
        try:
            chunk = json.loads(raw)
        except json.JSONDecodeError:
            return None
        choices = chunk.get("choices") or []
        if not choices:
            return None
        return (choices[0].get("delta") or {}).get("content") or None

    # ------------------------------------------------------------------
    # 对外
    # ------------------------------------------------------------------
    def invoke(self, messages: Iterable, **_: object) -> ChatMessage:
        """一次性获取完整回复（失败按 max_retries 重试）。"""
        last: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.http.post("chat/completions",
                                      json=self._payload(messages, False))
                if resp.status_code >= 400:
                    raise LLMError(
                        f"HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                data = resp.json()
                return ChatMessage(
                    (data["choices"][0].get("message") or {}).get("content") or ""
                )
            except Exception as e:  # noqa: BLE001 统一收敛后重试/抛出
                last = e
                if attempt < self.max_retries:
                    logger.warning("LLM 调用失败（第 %d 次），重试: %s",
                                   attempt + 1, str(e)[:160])
                    time.sleep(0.5 * (attempt + 1))
        raise LLMError(f"LLM 调用失败: {last}") from last

    def stream(self, messages: Iterable, **_: object) -> Iterator[ChatMessage]:
        """流式获取回复（逐块 yield）。"""
        with self.http.stream(
            "POST", "chat/completions", json=self._payload(messages, True)
        ) as resp:
            if resp.status_code >= 400:
                raise LLMError(f"HTTP {resp.status_code}: {resp.read()[:300]!r}")
            for line in resp.iter_lines():
                piece = self.parse_sse_line(line)
                if piece:
                    yield ChatMessage(piece)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
