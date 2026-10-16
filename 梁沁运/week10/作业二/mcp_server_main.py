import asyncio
from fastmcp import FastMCP, Client

from dice_and_news import mcp as dice_and_news_mcp
from news import mcp as news_mcp
from saying import mcp as saying_mcp
from tool import mcp as tool_mcp

mcp = FastMCP(
    name="MCP-Server"
)

async def setup():
    mcp.mount(news_mcp) # mcp server 可以相互挂载，最终 mcp server 对外提供服务
    mcp.mount(saying_mcp)
    mcp.mount(tool_mcp)
    mcp.mount(dice_and_news_mcp)

async def test_filtering():
    async with Client(mcp) as client:
        tools = await client.list_tools()
        print("Available tools:", [t.name for t in tools])


if __name__ == "__main__":
    asyncio.run(setup())
    asyncio.run(test_filtering())
    mcp.run(transport="sse", port=8000)
