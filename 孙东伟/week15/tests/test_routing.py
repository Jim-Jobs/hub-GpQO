"""路由层单测：不调用任何模型，验证「分类 → 分支」是否正确。

这是两套引擎共同的契约测试——同一输入必须得到同一标签、同一条执行路径。
"""

from __future__ import annotations

import pytest

from app.engines.agents_engine import AgentsEngine
from app.engines.langgraph_engine import LangGraphEngine
from app.mock import MockBrain, detect_crisis
from app.schemas import SentimentLabel

POSITIVE_TEXT = "今天答辩通过了，导师还夸我思路清楚，太开心了！"
NEGATIVE_TEXT = "连续加班两周，项目还是被否了，感觉自己很没用。"
NEUTRAL_TEXT = "明天要去体检，有点紧张。"
CRISIS_TEXT = "我最近真的撑不住了，不想活了。"


@pytest.mark.parametrize(
    ("text", "label"),
    [
        (POSITIVE_TEXT, SentimentLabel.POSITIVE),
        ("拿到 offer 了，谢谢大家！", SentimentLabel.POSITIVE),
        (NEGATIVE_TEXT, SentimentLabel.NEGATIVE),
        ("最近失眠很严重，压力好大。", SentimentLabel.NEGATIVE),
        ("今天不开心。", SentimentLabel.NEGATIVE),
        (NEUTRAL_TEXT, SentimentLabel.NEUTRAL),
        ("随便聊聊，最近天气转凉了。", SentimentLabel.NEUTRAL),
    ],
)
def test_mock_classifier(text: str, label: SentimentLabel) -> None:
    assert MockBrain().analyze(text).label is label


def test_negation_flips_positive_word() -> None:
    sentiment = MockBrain().analyze("这次又没有通过，很不开心")
    assert sentiment.label is SentimentLabel.NEGATIVE


def test_crisis_keywords_detected() -> None:
    assert detect_crisis(CRISIS_TEXT)
    assert not detect_crisis("今天有点累")


def test_langgraph_graph_shape() -> None:
    engine = LangGraphEngine(brain=MockBrain())
    nodes = set(engine.graph.get_graph().nodes)
    assert {"analyze", "encourage", "comfort", "neutral", "crisis"} <= nodes


@pytest.mark.asyncio
async def test_langgraph_routes_positive() -> None:
    result = await LangGraphEngine(brain=MockBrain()).run(POSITIVE_TEXT)
    assert result.sentiment.label is SentimentLabel.POSITIVE
    assert result.trail == ["analyze", "encourage"]
    assert result.reply


@pytest.mark.asyncio
async def test_langgraph_routes_negative() -> None:
    result = await LangGraphEngine(brain=MockBrain()).run(NEGATIVE_TEXT)
    assert result.sentiment.label is SentimentLabel.NEGATIVE
    assert result.trail == ["analyze", "comfort"]


@pytest.mark.asyncio
async def test_langgraph_routes_neutral_and_crisis() -> None:
    neutral = await LangGraphEngine(brain=MockBrain()).run(NEUTRAL_TEXT)
    assert neutral.trail == ["analyze", "neutral"]

    crisis = await LangGraphEngine(brain=MockBrain()).run(CRISIS_TEXT)
    assert crisis.crisis is True
    assert crisis.trail == ["analyze", "crisis"]
    assert "12356" in crisis.reply


@pytest.mark.asyncio
async def test_agents_engine_same_routing() -> None:
    engine = AgentsEngine(brain=MockBrain())
    positive = await engine.run(POSITIVE_TEXT)
    negative = await engine.run(NEGATIVE_TEXT)
    neutral = await engine.run(NEUTRAL_TEXT)

    assert positive.trail == ["sentiment_analyzer", "encourager"]
    assert negative.trail == ["sentiment_analyzer", "comforter"]
    assert neutral.trail == ["sentiment_analyzer", "neutral_responder"]


@pytest.mark.asyncio
async def test_two_engines_agree_on_labels() -> None:
    lg = LangGraphEngine(brain=MockBrain())
    ag = AgentsEngine(brain=MockBrain())
    for text in (POSITIVE_TEXT, NEGATIVE_TEXT, NEUTRAL_TEXT, CRISIS_TEXT):
        a, b = await lg.run(text), await ag.run(text)
        assert a.sentiment.label == b.sentiment.label
        assert a.crisis == b.crisis
