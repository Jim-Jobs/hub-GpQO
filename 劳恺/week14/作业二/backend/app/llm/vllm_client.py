"""vLLM 客户端 —— 接口定义 + Mock 实现。

对应文档：``docs/01-架构设计.md` §2.2（llm-client）、``docs/06-RAG与多模态设计.md` §5。

接口覆盖三种用途（都是打同一个 OpenAI 兼容端点 ``/v1/chat/completions``）：

1. 问答流式生成（``stream=True``，逐 token 推 ``delta``）；
2. 图片 caption（Worker 用 Qwen3-VL 给每张图写 1~2 句描述）；
3. 普通问答 / 拼装答案。

真实实现（base64 图片、图文交替 content、SSE 解析）在 M3 补齐。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

#: OpenAI 兼容的多模态消息格式：content 可以是字符串，也可以是 content parts 列表
#: （``{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}``）。
LLMMessage = dict[str, Any]


@runtime_checkable
class LLMClient(Protocol):
    """vLLM 调用接口。"""

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        """非流式，返回完整文本。"""
        ...

    def chat_stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """流式，逐段产出增量文本。"""
        ...

    async def health(self) -> bool:
        """连通性自检。"""
        ...


class MockLLMClient:
    """桩实现：**读 Prompt 生成有意义的假回答**，按切片逐段产出。

    它不是"返回一句写死的话"—— 那样验证不了任何东西：

    * 解析 Prompt 里的 ``<img id="..." page="N" caption="...">`` 标记，
      **主动吐出对应的** ``[[cite:<image_id>]]`` 引用标记 → 引用协议
      （含跨 token 断裂的处理）可以脱离真实模型完整验证；
    * 解析 ``【来源：《文件名》第 N 页】``，把出处写进回答 → 图文交替
      的组装效果肉眼可查；
    * 没有可引用的图时退化为纯文字回答。

    这样 ``MOCK_MODE=true`` 就能验证**整条问答链路**（检索 → 装配 → 流式 →
    引用解析 → SSE 事件 → 会话落库），只有"回答内容是否正确"这一项无法验证。
    """

    def __init__(
        self,
        answer: str | None = None,
        cite_marker: str | None = None,
        chunk_size: int = 8,
    ) -> None:
        self._answer = answer
        self._cite_marker = cite_marker
        self._chunk_size = chunk_size

    # ---------- 从 Prompt 里提取线索 ----------

    @staticmethod
    def _iter_text(messages: Sequence[LLMMessage]) -> list[str]:
        """取**最后一条 user 消息**里的文本部分。

        只看 user 消息：system prompt 里有 few-shot 示例（也带【来源：...】标记），
        把它当资料会抓出示例里的假页码。历史轮次同理不是本次参考资料。
        """
        last_user: dict | None = None
        for message in messages:
            if message.get("role") == "user":
                last_user = message
        if last_user is None:
            return []

        content = last_user.get("content")
        if isinstance(content, str):
            return [content]
        texts: list[str] = []
        for part in content or []:
            if isinstance(part, dict) and part.get("type") == "text":
                texts.append(str(part.get("text", "")))
        return texts

    def _full_text(self, messages: Sequence[LLMMessage]) -> str:
        if self._answer is not None:
            return (
                f"{self._answer}{self._cite_marker}"
                if self._cite_marker
                else self._answer
            )

        joined = "\n".join(self._iter_text(messages))

        # ① Prompt 里的图片标记（图文交替组装时注入的）
        image_ids = re.findall(r'<img id="([A-Za-z0-9_\-:.]+)"', joined)
        # ② 出处标注 + 其后的正文段落
        passages = self._extract_passages(joined)

        if not passages and not image_ids:
            # 连图片都没有 —— 多半是给图片写 caption 的场景
            if any("描述这张图片" in text for text in self._iter_text(messages)):
                return "这是一张文档图片的示意内容。"
            return "（Mock 模型）参考资料中没有可引用的内容，无法回答该问题。"

        parts: list[str] = []
        # 先给**基于检索内容的正文**，再补图片引用 —— 与真实模型的行为一致
        for name, pages, text in passages[:2]:
            snippet = text.strip()
            if len(snippet) > 200:
                snippet = snippet[:200] + "……"
            parts.append(f"根据《{name}》第 {pages} 页：{snippet}\n")
        if not passages:
            parts.append("根据检索到的资料：\n")

        if image_ids:
            parts.append(f"图示信息见 [[cite:{image_ids[0]}]]。")
            if len(image_ids) > 1:
                parts.append(f"（另有 {len(image_ids) - 1} 张相关图片）")
        else:
            parts.append("（该段资料没有绑定图片）")

        return "".join(parts)

    @staticmethod
    def _extract_passages(joined: str) -> list[tuple[str, str, str]]:
        """从 Prompt 里抽出 ``(文件名, 页码, 正文)``。

        Prompt 里每个块的形态是::

            【来源：《财报.pdf》第 7 页】
            2024 年前三季度营收分别为 28.1 / 33.5 / 42.3 亿元
        """
        pattern = re.compile(
            r"【来源：《([^》]+)》第\s*([\d\-]+)\s*页】\s*\n?(.*?)(?=\n\s*<img |\n\s*【来源：|\Z)",
            re.DOTALL,
        )
        passages: list[tuple[str, str, str]] = []
        for match in pattern.finditer(joined):
            text = match.group(3).strip()
            if text:
                passages.append((match.group(1), match.group(2), text))
        return passages

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        return self._full_text(messages)

    async def chat_stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        text = self._full_text(messages)
        for start in range(0, len(text), self._chunk_size):
            yield text[start : start + self._chunk_size]

    async def health(self) -> bool:
        return True


class VLLMClient:
    """真实的 vLLM OpenAI 兼容客户端（M3 实现细节见 ``docs/06`` §5）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.llm_base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(300.0, connect=10.0),
        )

    def _payload(
        self,
        messages: Sequence[LLMMessage],
        stream: bool,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        return {
            "model": self._settings.llm_model,
            "messages": list(messages),
            "stream": stream,
            "max_tokens": max_tokens or self._settings.llm_max_tokens,
            "temperature": (
                self._settings.llm_temperature if temperature is None else temperature
            ),
        }

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        response = await self._client.post(
            "/chat/completions", json=self._payload(messages, False, max_tokens, temperature)
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def chat_stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, True, max_tokens, temperature)
        async with self._client.stream("POST", "/chat/completions", json=payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[len("data: ") :].strip()
                if data == "[DONE]":
                    break
                import json

                chunk = json.loads(data)
                for choice in chunk.get("choices", []):
                    text = choice.get("delta", {}).get("content")
                    if text:
                        yield text

    async def health(self) -> bool:
        try:
            response = await self._client.get("/models", timeout=5.0)
            return response.status_code < 500
        except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
            logger.warning("vLLM 探测失败", exc_info=True)
            return False

    async def close(self) -> None:
        await self._client.aclose()


def build_llm_client(settings: Settings) -> LLMClient:
    """按 ``MOCK_MODE``（可被 ``MOCK_LLM`` 单独覆盖）选择实现。"""
    if settings.use_mock_llm:
        # 不传固定答案 —— 让 Mock 读 Prompt 生成有意义的回答（见 MockLLMClient）
        return MockLLMClient()
    return VLLMClient(settings)
