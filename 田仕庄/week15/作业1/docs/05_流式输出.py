import time
import os
os.environ["OPENAI_API_KEY"] = os.environ["DASHSCOPE_API_KEY"]  # 从环境变量注入，勿硬编码
os.environ["OPENAI_BASE_URL"] = "https://dashscope.aliyuncs.com/compatible-mode/v1"

import asyncio
from agents import Agent, Runner, SQLiteSession
from agents import set_default_openai_api, set_tracing_disabled
set_default_openai_api("chat_completions")
set_tracing_disabled(True)

import asyncio
from openai.types.responses import ResponseTextDeltaEvent
from agents import Agent, Runner

async def main():
    agent = Agent(
        name="Joker",
        model="qwen-max",
        instructions="You are a helpful assistant.",
    )

    result = Runner.run_streamed(agent, input="帮我讲5个笑话")
    async for event in result.stream_events():
        if event.type == "raw_response_event" and isinstance(event.data, ResponseTextDeltaEvent):
            print(event.data.delta, end="", flush=True)
    """
    做了两层精准过滤：
        第一层判断事件类型是否为底层原始响应事件，过滤掉工具调用事件、状态事件等非文本输出内容；
        第二层校验事件数据类型是否是文本增量事件，确保只会处理模型返回的打字机文本片段。
    实时打印增量内容：
        用end=""避免每次打印自动换行，让所有文本片段拼接成连续的输出内容；
        用flush=True强制立即刷新缓冲区，无需等待输出攒满再显示，实现真正的实时打字效果。
    """


if __name__ == "__main__":
    asyncio.run(main())