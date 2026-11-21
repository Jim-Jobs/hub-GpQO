"""兜底内容。

当 LLM 调用失败或输出无法解析时，系统必须仍然返回一个可用回复，
不能让整个对话链路断掉。
"""

from __future__ import annotations

from backends.shared.constants.emotions import EmotionCategory

#: 情绪识别失败时的兜底回复。
FALLBACK_REPLIES: dict[EmotionCategory, str] = {
    EmotionCategory.POSITIVE: "听起来这段时间你状态不错，希望这份好心情能一直保持下去。",
    EmotionCategory.NEGATIVE: (
        "听起来你现在不太好受。我在这里陪着你，如果愿意的话，可以慢慢讲给我听。"
    ),
}

#: 关键词兜底情绪判定（仅在 LLM 完全不可用时使用，置信度给得很低）。
POSITIVE_KEYWORDS: tuple[str, ...] = (
    "开心",
    "高兴",
    "快乐",
    "兴奋",
    "感谢",
    "谢谢",
    "棒",
    "好极了",
    "顺利",
    "成功",
    "满足",
    "幸福",
    "太棒",
    "不错",
    "期待",
    "希望",
    "喜欢",
)

NEGATIVE_KEYWORDS: tuple[str, ...] = (
    "难过",
    "伤心",
    "焦虑",
    "压力",
    "担心",
    "害怕",
    "愤怒",
    "生气",
    "烦",
    "孤独",
    "失望",
    "沮丧",
    "崩溃",
    "累",
    "疲惫",
    "糟糕",
    "糟心",
    "无助",
    "痛苦",
    "难受",
)


def keyword_guess_emotion(text: str) -> EmotionCategory:
    """按关键词粗判情绪极性，负向关键词优先（用户更常表达不满）。"""
    positive_hits = sum(1 for word in POSITIVE_KEYWORDS if word in text)
    negative_hits = sum(1 for word in NEGATIVE_KEYWORDS if word in text)
    if negative_hits > positive_hits:
        return EmotionCategory.NEGATIVE
    if positive_hits > negative_hits:
        return EmotionCategory.POSITIVE
    return EmotionCategory.NEGATIVE


#: 危机信号关键词。命中时应当使用安全支持策略，而不是普通共情。
CRISIS_KEYWORDS: tuple[str, ...] = (
    "自杀",
    "自残",
    "轻生",
    "不想活",
    "活不下去",
    "结束生命",
    "伤害自己",
    "了结自己",
    "想死",
)


def has_crisis_signal(text: str) -> bool:
    """判断消息中是否出现需要安全支持策略的危机信号。"""
    return any(keyword in text for keyword in CRISIS_KEYWORDS)
