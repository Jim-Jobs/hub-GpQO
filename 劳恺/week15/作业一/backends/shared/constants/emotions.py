"""情绪分类常量：二分类 + 具体情绪枚举。"""

from __future__ import annotations

from enum import Enum


class EmotionCategory(str, Enum):
    """情绪极性，只有正向与负向两种。"""

    POSITIVE = "positive"
    NEGATIVE = "negative"


POSITIVE_EMOTION_TYPES: tuple[str, ...] = (
    "happiness",
    "excitement",
    "gratitude",
    "confidence",
    "relief",
)

NEGATIVE_EMOTION_TYPES: tuple[str, ...] = (
    "sadness",
    "anger",
    "anxiety",
    "fear",
    "frustration",
    "loneliness",
    "disappointment",
)

OTHER_EMOTION_TYPE = "other"

EMOTION_TYPES: tuple[str, ...] = (
    POSITIVE_EMOTION_TYPES + NEGATIVE_EMOTION_TYPES + (OTHER_EMOTION_TYPE,)
)

#: 具体情绪 -> 极性。用于校验模型输出的 emotion_type 与 emotion 是否自洽。
EMOTION_TYPE_CATEGORY: dict[str, EmotionCategory] = {
    **{name: EmotionCategory.POSITIVE for name in POSITIVE_EMOTION_TYPES},
    **{name: EmotionCategory.NEGATIVE for name in NEGATIVE_EMOTION_TYPES},
    OTHER_EMOTION_TYPE: EmotionCategory.POSITIVE,  # other 无极性含义，校验时放宽
}
