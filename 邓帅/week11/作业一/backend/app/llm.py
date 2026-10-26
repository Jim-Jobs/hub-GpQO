"""LLM 客户端:OpenAI 兼容接口(AsyncOpenAI),凭据来自目录链上的 .env。"""
from openai import AsyncOpenAI

from app.config import settings

# 模块级共享客户端(连接复用)
_client = AsyncOpenAI(
    api_key=settings.api_key,
    base_url=settings.base_url,
    timeout=settings.llm_timeout,
)


async def chat(prompt: str, system: str | None = None, temperature: float = 0.2) -> str:
    """单轮对话,返回文本内容(strip 后)。

    prompt: 用户消息(作业各步的模板已格式化好的完整指令)
    system: 可选系统人设
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    resp = await _client.chat.completions.create(
        model=settings.base_model,
        messages=messages,
        temperature=temperature,
    )
    return (resp.choices[0].message.content or "").strip()
