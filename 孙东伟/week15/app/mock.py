"""离线规则引擎（mock）。

存在的意义有三个：
1. 没有 API Key 时也能完整演示「输入 → 分类 → 分支 → 回应」的链路；
2. 单元测试不需要联网、不烧 token，路由逻辑可以被确定性地验证；
3. 真实模型调用失败时可以降级，保证演示不中断。

它只做关键词打分，不代表真实模型能力，响应里会标注 ``mode=mock``。
"""

from __future__ import annotations

from app.prompts import CRISIS_KEYWORDS
from app.schemas import Sentiment, SentimentLabel

POSITIVE_WORDS: tuple[str, ...] = (
    "开心", "高兴", "快乐", "升职", "加薪", "通过", "成功", "顺利", "喜欢", "谢谢",
    "感谢", "幸福", "期待", "兴奋", "感动", "满足", "放松", "骄傲", "上岸", "毕业",
    "拿到", "赢了", "offer", "录取", "进步", "被夸", "好棒", "不错", "值得",
)

NEGATIVE_WORDS: tuple[str, ...] = (
    "难过", "伤心", "焦虑", "压力", "失眠", "分手", "失业", "失败", "好累", "心累",
    "烦", "崩溃", "委屈", "孤独", "哭", "被骂", "内耗", "emo", "痛苦", "生气",
    "愤怒", "失望", "害怕", "担心", "生病", "吵架", "挂科", "裁员", "抑郁", "无助",
    "难受", "郁闷", "迷茫", "内疚", "后悔", "被拒", "拖延", "烦躁",
    "加班", "被否", "没用", "撑不住", "差劲", "心慌", "焦躁", "疲惫", "被否定",
)

#: 否定词 + 正向词 的组合要判成 negative（如"不开心""没通过""不想干了"）。
NEGATIONS: tuple[str, ...] = ("不想", "没有", "不", "没", "别", "无", "未")

#: "特别""区别""告别"里的"别"不是否定词，避免误伤。
_FALSE_NEGATION_PREFIXES: tuple[str, ...] = ("特", "区", "告", "个", "分", "级", "性", "识", "派")

_GENERIC_HINTS: tuple[str, ...] = ("今天", "最近", "工作", "学习", "朋友", "考试", "项目", "身体")


def detect_crisis(text: str) -> bool:
    return any(word in text for word in CRISIS_KEYWORDS)


def _hits(text: str, words: tuple[str, ...]) -> list[str]:
    return [word for word in words if word in text]


def _negated(text: str, word: str) -> bool:
    """判断正向词前是否紧跟否定词，如"不开心""没通过"。"""
    start = text.find(word)
    if start <= 0:
        return False
    prefix = text[max(0, start - 3) : start]
    for neg in NEGATIONS:
        if prefix.endswith(neg):
            if neg == "别" and prefix[: -len(neg)].endswith(_FALSE_NEGATION_PREFIXES):
                continue
            return True
    return False


class MockBrain:
    """实现与真实引擎一致的 analyze / reply 接口。"""

    mode = "mock"

    def analyze(self, text: str) -> Sentiment:
        if detect_crisis(text):
            return Sentiment(
                label=SentimentLabel.NEGATIVE,
                confidence=0.99,
                reason="出现自伤相关表达，按安全策略直接判为负面并进入危机回应。",
                keywords=[w for w in CRISIS_KEYWORDS if w in text],
            )

        positives = _hits(text, POSITIVE_WORDS)
        negatives = _hits(text, NEGATIVE_WORDS)
        negated_positives = [w for w in positives if _negated(text, w)]

        score = len(positives) - len(negated_positives) * 2 - len(negatives)
        keywords = [w for w in positives if w not in negated_positives][:3] + negatives[:3]
        keywords += negated_positives[:2]

        if score > 0:
            label = SentimentLabel.POSITIVE
            reason = f"文本中出现正向表达：{'、'.join(positives[:3])}。"
        elif score < 0:
            label = SentimentLabel.NEGATIVE
            hit = (negatives or negated_positives)[:3]
            reason = f"文本中出现负向表达：{'、'.join(hit)}。"
        else:
            label = SentimentLabel.NEUTRAL
            reason = "没有命中明显正负向词，情绪强度不足以判定。"

        confidence = 0.62 + 0.07 * min(abs(score), 4)
        return Sentiment(
            label=label,
            confidence=round(min(confidence, 0.93), 2),
            reason=reason,
            keywords=keywords[:5],
        )

    def reply(self, text: str, sentiment: Sentiment, crisis: bool = False) -> str:
        from app.prompts import CRISIS_PROMPT  # 仅用于说明文案来源，实际文案见下

        if crisis:
            return (
                "我听到了，你现在一定非常难受，谢谢你愿意说出来。你不需要一个人扛着，"
                "请立刻联系一个你信任的人陪着你，也可以拨打全国心理援助热线 12356，"
                "紧急情况请拨打 120 或 110。我会在这里。"
            )

        keyword = sentiment.keywords[0] if sentiment.keywords else "这件事"
        variant = len(text) % 3

        if sentiment.label is SentimentLabel.POSITIVE:
            return (
                f"看到你说「{keyword}」，能感觉到你是真的开心。这不是运气，是你之前一点点攒出来的。"
                "愿意多讲讲当时最让你有成就感的那个瞬间吗？"
            )
        if sentiment.label is SentimentLabel.NEGATIVE:
            return (
                f"「{keyword}」这几个字背后，应该压了不少东西。先别急着解决它，"
                "你现在的感受是真实的，不需要马上变得积极。"
                "如果今晚只做一件小事，你愿意试着把它写下来，或者找个人说十分钟吗？"
            )
        return (
            "我大概明白你的意思了。你现在最想聊的是哪一部分？"
            "可以多说一点背景，我陪你一起理一理。"
        )
