"""共享评测数据模型；字段约束用于校验样例和模型运行记录。"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SentimentLabel(str, Enum):
    """项目使用的三种情感标签。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class TestCase(BaseModel):
    """一条人工标注的合成中文情感测试样例。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^C\d{3}$")
    text: str = Field(min_length=1, max_length=4000)
    expected_sentiment: SentimentLabel
    tags: list[str] = Field(default_factory=list)
    annotation_note: str = ""


class EvaluationRecord(BaseModel):
    """一次 Agent 运行的分类、回复、格式、性能及人工评分记录。"""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^C\d{3}$")
    run_id: str = Field(min_length=1)
    framework: Literal["langgraph", "openai-agents"]
    status: Literal["completed", "failed"]
    predicted_sentiment: SentimentLabel | None = None
    format_valid: bool | None = None
    reply: str | None = None
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    human_scores: dict[str, float] = Field(default_factory=dict)
    error: str | None = None
