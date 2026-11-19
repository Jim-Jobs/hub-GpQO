"""响应策略常量。

策略与情绪极性的对应关系是质量检查的核心规则：给处于负向情绪的用户
"加油""你可以的"这类鼓励话术是不合适的，会被判为不一致。
"""

from __future__ import annotations

from enum import Enum

from backends.shared.constants.emotions import EmotionCategory


class ResponseStrategy(str, Enum):
    """Agent 2 采用的响应策略。"""

    ENCOURAGEMENT = "encouragement"  # 正向：鼓励与庆祝
    EMPATHY = "empathy"  # 共情与接纳
    SUPPORT = "support"  # 负向：安全支持与求助引导


ALLOWED_STRATEGIES_BY_CATEGORY: dict[EmotionCategory, frozenset[ResponseStrategy]] = {
    EmotionCategory.POSITIVE: frozenset(
        {ResponseStrategy.ENCOURAGEMENT, ResponseStrategy.EMPATHY}
    ),
    EmotionCategory.NEGATIVE: frozenset(
        {ResponseStrategy.EMPATHY, ResponseStrategy.SUPPORT}
    ),
}

#: 负向情绪下推荐的默认策略。
DEFAULT_STRATEGY_BY_CATEGORY: dict[EmotionCategory, ResponseStrategy] = {
    EmotionCategory.POSITIVE: ResponseStrategy.ENCOURAGEMENT,
    EmotionCategory.NEGATIVE: ResponseStrategy.EMPATHY,
}
