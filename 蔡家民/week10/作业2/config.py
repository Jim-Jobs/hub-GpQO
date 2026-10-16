"""模型配置：客户端与翻译工具使用同一组环境变量。"""
import os

from agents import OpenAIChatCompletionsModel, set_tracing_disabled
from openai import AsyncOpenAI

set_tracing_disabled(True)


def create_model() -> tuple[AsyncOpenAI, OpenAIChatCompletionsModel]:
    key = os.getenv("OPENAI_API_KEY") or os.getenv("DASHSCOPE_API_KEY")
    if not key:
        raise ValueError("请先设置 OPENAI_API_KEY（或 DASHSCOPE_API_KEY）环境变量。")
    client = AsyncOpenAI(
        api_key=key,
        base_url=os.getenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        timeout=60.0,
        max_retries=1,
    )
    return client, OpenAIChatCompletionsModel(
        model=os.getenv("MODEL_NAME", "qwen-max"), openai_client=client
    )
