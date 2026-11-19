"""进程1 与进程2 的 Agent 定义。"""
import json
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from agents import Agent

from app.config import settings

logger = logging.getLogger(__name__)


class SentimentResult(BaseModel):
    """进程1 的输出（Agent 要求模型输出同样的 JSON，这里负责校验）。"""
    label: Literal["positive", "negative", "neutral"] = Field(description="情感倾向")
    score: float = Field(description="情感强度 0~1", ge=0, le=1)


# 进程1：情感识别 Agent（要求只输出 JSON，接口侧负责解析校验）
sentiment_agent = Agent(
    name="情感识别",
    model=settings.chat_model,
    instructions=(
        "你是情感识别模块。对用户输入只做情感判断，不要回复用户。"
        '只输出 JSON：{"label": "positive|negative|neutral", "score": 0到1的小数（情感强度）}，'
        "不要输出其他内容。"
    ),
)

# 进程2：正向 → 鼓励；负面/中性 → 排解（由后端进程按识别结果选择）
encourage_agent = Agent(
    name="鼓励师",
    model=settings.chat_model,
    instructions="用户分享了开心、积极的事。用一两句中文真诚地鼓励他，不要空洞套话。",
)

comfort_agent = Agent(
    name="安慰师",
    model=settings.chat_model,
    instructions="用户遇到了烦心事或情绪低落。用两三句中文排解他的情绪：先共情，再给一个具体的宽慰或建议，不要说教。",
)


def parse_sentiment(raw: str) -> SentimentResult | None:
    """解析进程1 的 JSON 输出，容忍 ```json 围栏；失败返回 None。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    try:
        return SentimentResult.model_validate(json.loads(text))
    except (ValueError, ValidationError):
        logger.warning("情感识别输出解析失败：%s", raw[:120])
        return None
