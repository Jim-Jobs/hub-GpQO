"""情绪识别结果模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from backends.shared.constants.emotions import (
    EMOTION_TYPE_CATEGORY,
    EMOTION_TYPES,
    OTHER_EMOTION_TYPE,
    EmotionCategory,
)


class EmotionResult(BaseModel):
    """Agent 1 的输出。

    ``emotion`` 只有 positive / negative 两种；``emotion_type`` 是更细粒度的
    情绪，模型无法判断时填 ``other`` 并相应降低 ``confidence``。
    """

    emotion: EmotionCategory
    confidence: float = Field(ge=0.0, le=1.0)
    emotion_type: str = OTHER_EMOTION_TYPE
    reason: str = ""

    @field_validator("emotion_type", mode="before")
    @classmethod
    def _normalize_emotion_type(cls, value: object) -> str:
        if not isinstance(value, str):
            return OTHER_EMOTION_TYPE
        cleaned = value.strip().lower()
        return cleaned if cleaned in EMOTION_TYPES else OTHER_EMOTION_TYPE

    @field_validator("reason", mode="before")
    @classmethod
    def _normalize_reason(cls, value: object) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()

    def demote_to_other(self, reason_suffix: str = "") -> EmotionResult:
        """当具体情绪与极性矛盾时降级为 ``other``（README 第 10 节规则）。"""
        return self.model_copy(
            update={
                "emotion_type": OTHER_EMOTION_TYPE,
                "confidence": min(self.confidence, 0.6),
                "reason": f"{self.reason}{reason_suffix}".strip(),
            }
        )

    def is_consistent(self) -> bool:
        """具体情绪与极性是否自洽（``other`` 视为自洽）。"""
        if self.emotion_type == OTHER_EMOTION_TYPE:
            return True
        return EMOTION_TYPE_CATEGORY.get(self.emotion_type) == self.emotion
