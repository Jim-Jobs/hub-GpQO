"""任务 1.3：openai-agents 0.20.0 自定义 base_url 预案最小验证。

验证点：
  1. `set_default_openai_client(AsyncOpenAI(api_key=..., base_url=...))`
     + `set_tracing_disabled(True)` 组合可让 Agent/Runner 正常调用
     项目根 `.env` 指向的 OpenAI 兼容模型（不触发默认 OpenAI 云端与
     tracing 上报）；
  2. `output_type` 结构化输出（SentimentResult 前身）可用；
  3. OpenAI 客户端 `max_retries=0`（重试统一交给 Temporal）不拦截正常调用。

运行：/opt/miniconda3/envs/py312/bin/python verify_openai_agents.py
"""

from __future__ import annotations

import asyncio
import os

from agents import Agent, Runner, set_default_openai_client, set_tracing_disabled
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

# load_dotenv 自脚本位置向上查找项目根 .env（badou2026/.env）
load_dotenv()


class Echo(BaseModel):
    """最小结构化输出 schema。"""

    word: str = Field(min_length=1, max_length=50)


async def main() -> None:
    model = os.getenv("BASE_MODEL")
    if not model:
        raise SystemExit("BASE_MODEL 未配置：检查项目根 .env")

    client = AsyncOpenAI(
        api_key=os.getenv("API_KEY"),
        base_url=os.getenv("BASE_URL"),
        max_retries=0,
    )
    set_default_openai_client(client)
    set_tracing_disabled(True)

    agent = Agent(
        name="验证器",
        instructions="从用户输入中提取最核心的一个名词，以 word 字段返回。",
        model=model,
        output_type=Echo,
    )
    result = await Runner.run(agent, "今天面试通过了，特别开心")
    print(f"model: {model}")
    print(f"output: {result.final_output}")


if __name__ == "__main__":
    asyncio.run(main())
