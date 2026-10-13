import os
import random
import signal
import logging
from typing import Union

import httpx
from dotenv import load_dotenv
from fastmcp import FastMCP
from openai import OpenAI

load_dotenv()

# ---------------- 日志 ----------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-server")

# ---------------- MCP 实例（改名） ----------------
mcp = FastMCP(name="ToolServer")

# ---------------- LLM 客户端（超时 + 重试） ----------------
_LLM_MODEL = os.getenv("OPENAI_MODEL", "deepseek-chat")
_llm_client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url=os.environ["OPENAI_BASE_URL"],
    timeout=float(os.getenv("LLM_TIMEOUT", "60")),
    max_retries=int(os.getenv("MAX_RETRIES", "3")),  # openai SDK 自带退避重试
)

# 天气代码 -> 中文描述
_WEATHER_CODE_MAP = {
    0: "晴", 1: "晴间多云", 2: "多云", 3: "阴",
    45: "雾", 48: "冻雾",
    51: "小毛毛雨", 53: "毛毛雨", 55: "浓毛毛雨",
    61: "小雨", 63: "中雨", 65: "大雨",
    71: "小雪", 73: "中雪", 75: "大雪",
    80: "小阵雨", 81: "阵雨", 82: "强阵雨",
    95: "雷雨", 96: "雷阵雨伴小冰雹", 99: "雷阵雨伴冰雹",
}


# ============================================================
# 原有工具
# ============================================================
@mcp.tool
def roll_dice(n_dice: int) -> list[int]:
    """Roll `n_dice` 6-sided dice (values 1-6) and return the results."""
    if n_dice <= 0:
        raise ValueError(f"n_dice must be positive, got {n_dice}")
    if n_dice > 100:
        raise ValueError(f"n_dice too large (max 100), got {n_dice}")
    return [random.randint(1, 6) for _ in range(n_dice)]


@mcp.tool
def get_news() -> Union[int, str]:
    """Get latest news."""
    return "最新消息：DeepSeek-V4 即将发布！"


# =============================================================================
# 新增工具：1、查询天气（网络异常兜底）；2、创作短篇小说；3、引申历史人物的有关事件
# =============================================================================
@mcp.tool
async def get_weather(city: str) -> str:
    """根据输入的城市名，查询该城市当天的天气情况。"""
    city = (city or "").strip()
    if not city:
        raise ValueError("city 不能为空")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            geo_resp = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": city, "count": 1, "language": "zh"},
            )
            geo_resp.raise_for_status()
            results = geo_resp.json().get("results") or []
            if not results:
                return f"未找到城市：{city}"

            loc = results[0]
            lat, lon = loc["latitude"], loc["longitude"]

            w_resp = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lon,
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code",
                    "timezone": "auto",
                },
            )
            w_resp.raise_for_status()
            current = w_resp.json().get("current", {})
    except httpx.HTTPError as e:
        logger.exception("查询 %s 天气 HTTP 失败", city)
        return f"查询 {city} 天气失败（网络错误）：{e}"
    except Exception as e:  # 兜底，避免工具抛异常打断 MCP 会话
        logger.exception("查询 %s 天气未知错误", city)
        return f"查询 {city} 天气失败：{e}"

    code = current.get("weather_code")
    desc = _WEATHER_CODE_MAP.get(code, f"天气代码 {code}")
    return (
        f"{city}（{loc.get('country', '')} {loc.get('admin1', '')}）"
        f"当前天气：{desc}，温度 {current.get('temperature_2m')}°C，"
        f"相对湿度 {current.get('relative_humidity_2m')}%，"
        f"风速 {current.get('wind_speed_10m')} km/h。"
    )


# ============================================================
# LLM 辅助（统一超时、重试、异常兜底）
# ============================================================
def _call_llm(prompt: str) -> str:
    try:
        resp = _llm_client.chat.completions.create(
            model=_LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.exception("LLM 调用失败")
        return f"生成失败：{e}"


@mcp.tool
def write_short_story(topic: str) -> str:
    """根据输入的主题创作一篇 1000 字以内的短篇小说。"""
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("topic 不能为空")
    prompt = (
        f"请根据主题「{topic}」创作一篇短篇小说。\n"
        f"要求：\n"
        f"1. 字数不超过 1000 字。\n"
        f"2. 有清晰的开端、发展、结尾，人物和情节完整。\n"
        f"3. 语言生动，有画面感。\n"
        f"直接输出小说正文，不要加标题或额外说明。"
    )
    return _call_llm(prompt)


@mcp.tool
def query_historical_events(person: str) -> str:
    """根据输入的历史人名，查询与其相关的重大历史事件。"""
    person = (person or "").strip()
    if not person:
        raise ValueError("person 不能为空")
    prompt = (
        f"请介绍历史人物「{person}」相关的重大历史事件。\n"
        f"要求：\n"
        f"1. 按时间顺序列出 3-5 件最具代表性的事件。\n"
        f"2. 每件用一两句话说明发生背景和历史影响。\n"
        f"3. 语言简洁客观。\n"
        f"直接输出结果，不要加额外说明。"
    )
    return _call_llm(prompt)


# ============================================================
# 优雅关闭
# ============================================================
def _handle_signal(signum, _frame):
    logger.info("收到信号 %s，准备退出", signum)
    raise KeyboardInterrupt()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    port = int(os.getenv("MCP_SERVER_PORT", "8000"))
    logger.info("启动 MCP Server: %s，端口 %d，模型 %s", mcp.name, port, _LLM_MODEL)
    try:
        mcp.run(transport="sse", port=port)
    except KeyboardInterrupt:
        logger.info("MCP Server 已停止")
    finally:
        logger.info("资源清理完成")