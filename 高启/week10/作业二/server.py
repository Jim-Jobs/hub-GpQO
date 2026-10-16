import random
from fastmcp import FastMCP

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
def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"{city} 的天气是晴天"

@mcp.tool
def get_exchange_rate(currency: str) -> float:
    """Get exchange rate for a given currency."""
    rates = {
        "USD": 1.0,
        "EUR": 0.85,
        "JPY": 110.0,
        "CNY": 6.5,
    }
    return rates.get(currency.upper(), 1.0)

@mcp.tool
def get_holiday(date: str) -> str:
    """Get holiday information for a given date."""
    holidays = {
        "2024-01-01": "New Year's Day",
        "2024-07-04": "Independence Day",
        "2024-12-25": "Christmas Day",
    }
    return holidays.get(date, "No holiday on this date.")

if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)