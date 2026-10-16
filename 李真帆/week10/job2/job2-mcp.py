from openai import AsyncOpenAI
from agents import Agent, Runner, set_default_openai_client, set_default_openai_api, set_tracing_disabled
from agents.mcp import MCPServerSse

# ========== 配置自定义模型服务商 ==========
client = AsyncOpenAI(
    base_url="https://api.deepseek.com/v1",  # DeepSeek 的 API 地址
    api_key="***",  # 替换为你的实际 key
)
set_default_openai_client(client, use_for_tracing=False)
set_default_openai_api("chat_completions")  # DeepSeek 使用 Chat Completions 协议
set_tracing_disabled(True)  # 关闭 tracing，消除提示日志
# ==========================================

mcp_server = MCPServerSse(
    name="McpServer",
    params={
        "url": "http://localhost:8000/sse"
    },
    cache_tools_list=True
)

mcp_agent = Agent(
    name="McpAgent",
    instructions="",
    model="deepseek-chat",
    mcp_servers=[mcp_server]
)


async def main(query:str):
    await mcp_server.connect()
    try:
        result = await Runner.run(mcp_agent, query)
        print(result.final_output)
    finally:
        await mcp_server.cleanup()


if __name__ == '__main__':
    import asyncio
    asyncio.run(main("杭州和北京都是什么天气"))
    print("*"*30)
    asyncio.run(main("小明和小红都考了多少分?"))
    print("*"*30)
    asyncio.run(main("请帮我翻译Machine Learning"))
