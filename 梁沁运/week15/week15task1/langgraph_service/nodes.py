"""
逻辑层（Logic Layer）—— 后端 A：LangGraph 服务

【重要约束】
本文件是整个 langgraph_service 中**唯一调用大模型**的地方，
且只允许在下面三个节点函数中发起模型请求：
  - analyze_sentiment        进程 1（识别）
  - generate_encouragement   进程 2（生成 · 正向）
  - generate_console         进程 2（生成 · 负向）

模型配置全部来自 .env（python-dotenv 加载），禁止硬编码。
"""

import os
import re
from typing import Literal

import openai
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from schemas import AgentState

# 加载 .env（显式指定本文件所在目录的 .env，保证从任意目录启动都能读到；
# 文件不存在时不会报错，继续使用系统环境变量）
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_SERVICE_DIR, ".env"))

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash"

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
SILICONFLOW_MODEL = os.getenv("SILICONFLOW_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
SILICONFLOW_BASE_URL = os.getenv(
    "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
).strip()

if not SILICONFLOW_API_KEY:
    raise RuntimeError(
        "未检测到 SILICONFLOW_API_KEY，请在 langgraph_service/.env 中配置该环境变量。"
    )

# ChatOpenAI 是同步客户端；本项目统一使用同步调用，不使用 async/await
llm = ChatOpenAI(
    model=SILICONFLOW_MODEL,
    api_key=SILICONFLOW_API_KEY,
    base_url=SILICONFLOW_BASE_URL,
    temperature=0.7,
    # 显式使用 openai SDK 自带的同步 httpx 客户端：
    # langchain-openai 默认自建 httpx.Client，在 openai 3.x（基于 httpx2）下
    # 会出现 transport 类型不匹配。用 SDK 自带的客户端即可保持一致。
    http_client=openai.DefaultHttpx2Client()
    if hasattr(openai, "DefaultHttpx2Client")
    else None,
)


def _extract_sentiment(text: str) -> Literal["positive", "negative"]:
    """
    从模型输出中解析情感标签。

    模型可能返回 "positive"、带标点的 "Positive."、或一段解释文字，
    因此做大小写无关的包含匹配；无法判定时保守兜底为 negative。
    """
    lowered = text.strip().lower()
    if re.search(r"\bpositive\b", lowered):
        return "positive"
    if re.search(r"\bnegative\b", lowered):
        return "negative"
    # 兜底：负向是需要被接住的那一侧，误判为 positive 的代价更高
    return "negative"


# ---------------------------------------------------------------------------
# 进程 1（识别）
# ---------------------------------------------------------------------------
def analyze_sentiment(state: AgentState) -> dict:
    """
    情感识别节点：调用大模型判断用户输入是 positive 还是 negative。

    返回 {"sentiment": ...}，只更新 sentiment 字段。
    """
    user_input = state["user_input"]

    prompt = (
        "你是一个情感分类器。请判断下面这段用户文本的整体情感倾向是 positive（积极）"
        "还是 negative（消极）。只回答一个英文单词：positive 或 negative，不要输出任何其他内容。\n\n"
        f"用户文本：{user_input}"
    )

    result = llm.invoke(prompt)
    content = result.content if isinstance(result.content, str) else str(result.content)
    sentiment = _extract_sentiment(content)

    return {"sentiment": sentiment}


# ---------------------------------------------------------------------------
# 进程 2（生成）
# ---------------------------------------------------------------------------
def generate_encouragement(state: AgentState) -> dict:
    """
    鼓励节点（正向）：读取 user_input，生成鼓励话术。

    返回 {"response": ...}，只更新 response 字段。
    """
    user_input = state["user_input"]

    prompt = (
        "你是一位温暖的情感陪伴助手。用户当前情绪积极，请针对用户分享的内容，"
        "用中文写一段真诚、具体的鼓励话术（2~4 句话），认可对方的感受，"
        "并给予积极的回应。不要提及情感分类。\n\n"
        f"用户文本：{user_input}"
    )

    result = llm.invoke(prompt)
    response = result.content if isinstance(result.content, str) else str(result.content)

    return {"response": response.strip()}


def generate_console(state: AgentState) -> dict:
    """
    排解节点（负向）：读取 user_input，生成排解、安慰话术。

    返回 {"response": ...}，只更新 response 字段。
    """
    user_input = state["user_input"]

    prompt = (
        "你是一位温柔、有同理心的情感陪伴助手。用户当前情绪消极，请针对用户分享的内容，"
        "用中文写一段安慰与排解的话术（2~4 句话）：先共情、接纳对方的情绪，"
        "再温和地给出一个可执行的小建议或换个视角的解读。"
        "不要说教，不要提及情感分类，也不要下诊断。\n\n"
        f"用户文本：{user_input}"
    )

    result = llm.invoke(prompt)
    response = result.content if isinstance(result.content, str) else str(result.content)

    return {"response": response.strip()}
