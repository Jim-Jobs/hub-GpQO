import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = os.environ.get("MINIMAX_API_KEY")
os.environ["OPENAI_BASE_URL"] = "https://api.minimaxi.com/v1"

from agents.mcp.server import MCPServerStreamableHttp
import asyncio
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents import set_default_openai_api, set_tracing_disabled
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


set_default_openai_api("chat_completions")
set_tracing_disabled(True)

async def run(mcp_server: MCPServer):
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )

    # openai agent
    agent = Agent(
        name="Assistant",
        instructions="",
        mcp_servers=[mcp_server], # tools
        model=OpenAIChatCompletionsModel(
            model="MiniMax-M3",
            openai_client=external_client,
        )
    )

    # list tool -> select tool -> execute tool -> return result -> gpt answer
    # list tool 得到 可选api 参数
    # select tool 本质就是function call
    message = "最近有什么新闻？"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    """
    step1，通过mcp client mcp list tool，得到mcp server 可选的工具、工具的参数、返回格式
    step2， 工具、工具的参数、返回格式 汇总为tools / functioncall 的格式 通过大模型 选择一个工具 -》 传入参数
    step3, 通过mcp client 调用execute tool 在mcp server 执行这个工具 -》 返回结果
    step4， 返回结果汇总在提示词，调用大模型得到结果
    """

    message = "10+60等于多少"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    message = "深圳天气怎么样"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)

    message = "今天日期多少"
    print(f"Running: {message}")
    result = await Runner.run(starting_agent=agent, input=message)
    print(result.final_output)


async def main():
    async with MCPServerStreamableHttp(
            name="http_mcp",
            params={
                "url": "http://127.0.0.1:8000/mcp",
            },
    ) as server:
        await run(server)

if __name__ == "__main__":
    asyncio.run(main())
