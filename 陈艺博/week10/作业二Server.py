from fastmcp import FastMCP

mcp = FastMCP(name="Dice Roller")

# 工具名字、传入参数类型、返回值类型、函数的注释（工具说明）
# 新增工具1：获取当前时间
@mcp.tool()
def get_current_time() -> str:
    """获取当前的系统时间"""
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

# 新增工具2：简单的计算器
@mcp.tool()
def calculate_sum(a: int, b: int) -> int:
    """计算两个整数的和"""
    return a + b

# 新增工具3：查询天气（模拟）
@mcp.tool()
def get_weather(city: str) -> str:
    """获取指定城市的天气信息"""
    return f"{city} 的天气是晴天，温度 25℃。"


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)