"""无 API Key 时使用的规则版情感识别与回复，保证作业可直接运行。"""

from __future__ import annotations

from .schemas import Sentiment, SentimentResult

POSITIVE_WORDS = (
    "开心", "高兴", "快乐", "兴奋", "顺利", "成功", "棒", "很好", "不错",
    "喜欢", "感谢", "谢谢", "自豪", "满足", "期待", "nice", "good", "great",
    "happy", "love", "win", "awesome", "excellent", "👍", "😊", "🎉",
)
NEGATIVE_WORDS = (
    "难过", "伤心", "焦虑", "压力", "累", "疲惫", "沮丧", "失望", "失败",
    "生气", "烦", "痛苦", "委屈", "孤独", "担心", "害怕", "无助", "crash",
    "sad", "angry", "tired", "stressed", "fail", "bad", "hate", "anxious",
    "😢", "😡", "💔",
)

ENCOURAGE_TEMPLATES = (
    "听起来你正在经历一段很棒的时光！这份积极的状态值得被认真记住，继续保持，"
    "你完全有能力把这份好势头延续下去。",
    "真为你感到高兴！你付出的努力正在开花结果，请把这份喜悦分享给身边的人，"
    "也给自己一点肯定。",
)
COMFORT_TEMPLATES = (
    "听起来你现在承受了不少压力。先深呼吸一下，允许自己有这些情绪是很正常的。"
    "如果愿意，可以把困扰你的事情拆成小步骤，我们一步一步来。",
    "我能感受到你此刻的低落。你不需要一个人硬扛，先照顾好当下的自己，"
    "休息一下，等你愿意时再一起看看有没有可以调整的地方。",
)
NEUTRAL_TEMPLATES = (
    "我记下你的分享了。如果此刻没有特别的情绪，也没关系，"
    "我们可以一起梳理信息；当你想聊感受时，我随时在这里。"
)


def _pick(text: str, templates: tuple[str, ...]) -> str:
    return templates[sum(ord(ch) for ch in text) % len(templates)]


def mock_recognize(text: str) -> SentimentResult:
    """基于关键词的简易情感识别，用于演示流程。"""
    lower = text.lower()
    pos = sum(word.lower() in lower for word in POSITIVE_WORDS)
    neg = sum(word.lower() in lower for word in NEGATIVE_WORDS)

    if pos > neg:
        return SentimentResult(
            sentiment=Sentiment.POSITIVE,
            confidence=min(0.95, 0.65 + 0.1 * (pos - neg)),
            reason=f"命中 {pos} 个正向关键词、{neg} 个负向关键词（mock 规则识别）",
        )
    if neg > pos:
        return SentimentResult(
            sentiment=Sentiment.NEGATIVE,
            confidence=min(0.95, 0.65 + 0.1 * (neg - pos)),
            reason=f"命中 {neg} 个负向关键词、{pos} 个正向关键词（mock 规则识别）",
        )
    return SentimentResult(
        sentiment=Sentiment.NEUTRAL,
        confidence=0.6,
        reason="未发现明显的正向或负向关键词（mock 规则识别）",
    )


def mock_reply(sentiment: Sentiment, text: str) -> str:
    """按情感类别返回鼓励、排解或平和回应。"""
    if sentiment is Sentiment.POSITIVE:
        return _pick(text, ENCOURAGE_TEMPLATES)
    if sentiment is Sentiment.NEGATIVE:
        return _pick(text, COMFORT_TEMPLATES)
    return _pick(text, NEUTRAL_TEMPLATES)
