import random
from fastmcp import FastMCP
from datetime import datetime, timezone

mcp = FastMCP(name="Dice Roller")

# 工具名字、传入参数类型、返回值类型、函数的注释（工具说明）

@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice and return the results."""
    return [random.randint(10, 60) for _ in range(n_dice)]

@mcp.tool
def get_news() -> str:
    """Get latest news."""
    return "最新消息 Qwen4 is coming!"

@mcp.tool
def add(a:int, b:int) -> int:
    """计算两个数的和"""
    return a + b

@mcp.tool()
def get_weather(city: str = "深圳") -> str:
    """查询指定城市的天气"""
    return f"{city} 天气晴，28℃"

@mcp.tool()
def get_current_date(tz: str = "Asia/Shanghai") -> str:
    """获取当前日期"""
    try:
        now = datetime.now(timezone.utc).astimezone(
            datetime.now().astimezone().tzinfo
        )
        return datetime.now().strftime("%Y-%m-%d")
    except Exception as e:
        return f"获取时间失败：{e}"

if __name__ == "__main__":
    mcp.run(transport="streamable-http")
