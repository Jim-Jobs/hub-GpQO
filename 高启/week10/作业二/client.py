import os

# https://bailian.console.aliyun.com/?tab=model#/api-key
os.environ["OPENAI_API_KEY"] = "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

from agents.mcp.server import MCPServerSse
import asyncio
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents import set_default_openai_api, set_tracing_disabled
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
        instructions=(
            "你是一个生活助手，可以调用以下工具："
            "- 问最新的新闻 → get_news"
            "- 问城市的天气 → get_weather"
            "- 问节日日期、星期几 → get_holiday"
            "- 用户问汇率、货币换算 → get_exchange_rate\n"
            "拿到工具结果后，用自然语言简洁地回答用户。"
            ""
        ),
        mcp_servers=[mcp_server], # tools
        model=OpenAIChatCompletionsModel(
            model="deepseek-flash",
            openai_client=external_client,
        )
    )

    message = [
        "帮我查一下今天的新闻。",
        "获取北京的天气。",
        "100 美元能换多少人民币？",
        "2025 年国庆节是星期几？",
    ]
    for message in message:
        print(f"Running: {message}")
        result = await Runner.run(starting_agent=agent, input=message)
        print(result.final_output)


async def main():
    async with MCPServerSse(
            name="SSE Python Server",
            params={
                "url": "http://localhost:8000/sse", # mcp server 通过http 方式沟通
            },
    )as server:
        await run(server)

if __name__ == "__main__":
    asyncio.run(main())
