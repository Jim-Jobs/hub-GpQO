# -*- coding: utf-8 -*-
"""
作业2：MCP Client（基于 2-兼容OpenAI-Agent 的 client.py）

通过 openai-agents 框架连接上面的 MCP Server，让 Agent 根据用户自然语言问题
自动选择合适的工具（含新增的 3 个工具）并完成调用。

运行顺序：
    1. 先启动 server：  python server.py
    2. 再运行 client：  python client.py
"""

import os

# DeepSeek 官方 API（兼容 OpenAI 协议）
os.environ["OPENAI_API_KEY"] = "sk-7c1606376d4c4ac1ae86d1933e6bbca9"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

import asyncio

from agents import (
    Agent,
    AsyncOpenAI,
    OpenAIChatCompletionsModel,
    Runner,
    set_default_openai_api,
    set_tracing_disabled,
)
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerSse

set_default_openai_api("chat_completions")
set_tracing_disabled(True)


async def run(mcp_server: MCPServer):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    # 把 MCP Server 里所有工具（含新增 3 个）都暴露给 Agent
    agent = Agent(
        name="Assistant",
        instructions="你是一个助手，根据用户问题选择合适的 MCP 工具来完成任务，并总结回答。",
        mcp_servers=[mcp_server],  # tools
        model=OpenAIChatCompletionsModel(
            model="deepseek-chat",
            openai_client=external_client,
        ),
    )

    # 每个问题对应一个工具；最后一个问题需要"先掷骰子再求和"，验证多步编排
    queries = [
        "帮我计算 (3 + 5) * 2 的结果是多少？",                 # -> calculator
        "统计一下这句话有多少个字：今天天气真好，我们去公园散步吧",  # -> word_count
        "帮我生成一个 8 位的随机密码",                          # -> generate_password
        "摇3次骰子，并把每次的结果加起来告诉我",                  # -> roll_dice + 求和
    ]

    for message in queries:
        print(f"\nRunning: {message}")
        result = await Runner.run(starting_agent=agent, input=message)
        print(result.final_output)


async def main():
    # 通过 HTTP(SSE) 方式连接远程 MCP Server
    async with MCPServerSse(
        name="SSE Python Server",
        params={"url": "http://localhost:8000/sse"},
    ) as server:
        await run(server)


if __name__ == "__main__":
    asyncio.run(main())
