import random
from datetime import datetime
from typing import Union, Annotated
from zoneinfo import ZoneInfo

from fastmcp import FastMCP

mcp = FastMCP(name="Multi-Tools MCP Server")

# 工具名字、传入参数类型、返回值类型、函数的注释（工具说明）

@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]

@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"


# ---------- 作业2：新增 3 个 MCP 工具 ----------

@mcp.tool
def add(a: float, b: float) -> float:
    """Add two numbers and return the sum."""
    return a + b


@mcp.tool
def get_current_time() -> str:
    """Get the current date and time in Asia/Shanghai (UTC+8)."""
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    return now.strftime("%Y-%m-%d %H:%M:%S") + " (Asia/Shanghai)"


@mcp.tool
def get_weather(
    city_name: Annotated[str, "The pinyin of the city name, e.g. 'beijing', 'shanghai' or 'chengdu'."],
) -> str:
    """Get the current weather of a city (demo/simulated data, not a real forecast)."""
    demo = {
        "beijing": ("晴", 22, "东南风 2 级"),
        "shanghai": ("多云", 25, "东风 3 级"),
        "chengdu": ("阴天", 20, "微风"),
    }
    city = city_name.strip().lower()
    if city not in demo:
        return f"没有 {city_name} 的模拟天气数据，支持：beijing / shanghai / chengdu"
    condition, temp, wind = demo[city]
    return f"{city_name} 当前天气：{condition}，{temp}°C，{wind}（模拟数据）"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)