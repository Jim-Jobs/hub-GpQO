import random
from typing import Union
from fastmcp import FastMCP

mcp = FastMCP(
    name="DiceAndNews-MCP-Server",
    instructions="""This server contains dice rolling and the latest news tools.""",
)


@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]


@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"