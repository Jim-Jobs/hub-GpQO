import random
from fastmcp import FastMCP
from agents import Runner
from tdt import TextDetectionTranslationAgent

mcp = FastMCP()


@mcp.tool
def get_weather(city: str) -> str:
    if city == "杭州":
        return "晴天"
    else:
        return "雨天"


@mcp.tool
def get_score(name: str) -> int:
    if name == "小明":
        return 100
    else:
        return random.randint(55, 99)


@mcp.tool
def text_detection_translation(user_input: str) -> str:
    return Runner.run_sync(TextDetectionTranslationAgent.tdt_agent, user_input).final_output


if __name__ == "__main__":
    mcp.run(transport="sse", port=8000)
