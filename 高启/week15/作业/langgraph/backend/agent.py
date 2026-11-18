"""LangGraph 流程调用的两个 LLM Agent 节点及 DeepSeek 模型适配。"""

import os
from typing import Any

from backend.prompts import REPLY_SYSTEM_PROMPT, REPLY_USER_PROMPT, SENTIMENT_SYSTEM_PROMPT, SENTIMENT_USER_PROMPT
from backend.schemas import SentimentAgentOutput


def create_model() -> Any:
    """创建使用环境变量配置的 LangChain DeepSeek 聊天模型。

    Args:
        无；配置从 DeepSeek 三个环境变量读取。

    Returns:
        可调用 `invoke` 的 LangChain 聊天模型。

    Raises:
        KeyError: 必需的 DeepSeek 环境变量未设置。
        ImportError: LangChain OpenAI 适配依赖未安装。
    """
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=os.environ["DEEPSEEK_MODEL"],
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
        temperature=0.2,
    )


def parse_sentiment_output(content: str) -> SentimentAgentOutput:
    """校验识别 Agent 的 JSON 输出，不自动修复格式错误。

    Args:
        content: 模型返回的原始文本，应为无 Markdown 围栏的 JSON 对象。

    Returns:
        已校验的情感类别和判断原因。

    Raises:
        pydantic.ValidationError: JSON 无效、字段缺失、多字段或情感标签非法。
    """
    return SentimentAgentOutput.model_validate_json(content)


def classify_sentiment(state: dict[str, Any]) -> dict[str, Any]:
    """作为情感识别图节点调用模型并解析三分类结果。

    Args:
        state: 至少包含 `request_id` 和 `text` 的 LangGraph 状态。

    Returns:
        包含 `sentiment`、`reason` 的部分状态更新。

    Raises:
        pydantic.ValidationError: 模型输出不符合情感 JSON 契约。
        Exception: DeepSeek 请求失败。
    """
    prompt = SENTIMENT_USER_PROMPT.format(text=state["text"])
    response = create_model().invoke([
        ("system", SENTIMENT_SYSTEM_PROMPT),
        ("human", prompt),
    ])
    parsed = parse_sentiment_output(str(response.content))
    return {"sentiment": parsed.sentiment, "reason": parsed.reason}


def generate_reply(state: dict[str, Any]) -> dict[str, str]:
    """作为情绪回应图节点，根据识别结果生成中文回复。

    Args:
        state: 包含原文、情感类别和识别原因的 LangGraph 状态。

    Returns:
        包含 `reply` 的部分状态更新。

    Raises:
        Exception: DeepSeek 请求失败。
    """
    sentiment = state["sentiment"]
    prompt = REPLY_USER_PROMPT.format(
        text=state["text"],
        sentiment=sentiment.value if hasattr(sentiment, "value") else sentiment,
        reason=state["reason"],
    )
    response = create_model().invoke([
        ("system", REPLY_SYSTEM_PROMPT),
        ("human", prompt),
    ])
    return {"reply": str(response.content).strip()}
