"""响应策略选择策略。

两套 Agent 框架用**同一套规则**决定首选策略，因此两边行为可比；
框架自身的编排机制（LangGraph 的条件边 / Agents SDK 的 handoff）
只负责把这个首选策略落到具体的执行分支上。
"""

from __future__ import annotations

from backends.shared.constants.emotions import EmotionCategory
from backends.shared.constants.fallbacks import has_crisis_signal
from backends.shared.constants.strategies import ResponseStrategy
from backends.shared.schemas.emotion import EmotionResult


def choose_preferred_strategy(emotion: EmotionResult, message: str) -> ResponseStrategy:
    """根据情绪极性与危机信号选出首选响应策略。

    - 正向情绪 -> 鼓励
    - 负向情绪且出现危机信号 -> 安全支持
    - 其余负向情绪 -> 共情
    """
    if emotion.emotion == EmotionCategory.POSITIVE:
        return ResponseStrategy.ENCOURAGEMENT
    if has_crisis_signal(message):
        return ResponseStrategy.SUPPORT
    return ResponseStrategy.EMPATHY
