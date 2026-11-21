import time
import os

from openai import AsyncOpenAI

os.environ["OPENAI_API_KEY"] = os.environ["DASHSCOPE_API_KEY"]  # 从环境变量注入，勿硬编码
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

import asyncio
from agents import Agent, Runner, SQLiteSession, OpenAIChatCompletionsModel
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)

# 多轮对话 上下文，sqlite mysql
# 在相同agent  或 不同agent 之间共享历史对话
session = SQLiteSession("conversation_123")

# session 可以选择历史对话的最后10条作为上下文，一起送到大模型

async def main():
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )
    agent = Agent(
        name="Assistant",
        instructions="",
        model=OpenAIChatCompletionsModel(
            model="qwen-max",
            openai_client=external_client,
        )
    )
    # agent = Agent(
    #     name="Assistant",
    #     model="qwen-max",
    #     instructions="Reply very concisely.", # 系统提示词 system messgae
    # )
    
    result = await Runner.run(
        agent,
        "你好", # 用户的第一次输入 user message
        session=session
    )
    print(result.final_output) # assistant message
    print(await session.get_items(2))

    result = await Runner.run(
        agent,
        "我的上一句是什么？", # 用户第二次输入 user message
        session=session
    )
    print(result.final_output) # assistant message
    print(await session.get_items(2))

    result = await Runner.run(
        agent,
        "我是第一句是什么", # 用户第三次输入 user message
        session=session
    )
    print(result.final_output) # assistant message
    print(await session.get_items(2))


if __name__ == "__main__":
    asyncio.run(main())