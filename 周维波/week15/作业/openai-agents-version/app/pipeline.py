"""openai-agents 版流水线。

进程 1：情感识别 Agent（输出结构化 SentimentResult）
进程 2：按识别结果路由到「鼓励 / 排解 / 中性」三个 Agent 之一
"""

from __future__ import annotations

import logging
import re
import time

from agents import (
    Agent,
    Runner,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from .config import get_settings
from .schemas import ChatResponse, SentimentResult, StageTrace, SupportReply

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- 提示词

STAGE1_PROMPT = """你是一名情感识别专家（流水线第 1 步）。只判断情感，不给建议，不安慰用户。

请阅读用户文本后输出结构化结果：
- label：positive（正向）/ negative（负面）/ neutral（中性）
- confidence：0~1 的置信度
- intensity：情绪强度 1~5，1 为平淡，5 为强烈
- emotions：最多 3 个具体情绪词，如 喜悦、期待、焦虑、委屈、愤怒、疲惫
- reason：40 字以内说明判断依据，尽量引用关键措辞

判断要点：
1. 注意否定句与反讽，如"我不难过""真是太好了呢"，要结合整体语境判断真实倾向；
2. 混合情绪时以主导情绪决定 label，其余写入 emotions；
3. 客观陈述事实、几乎无情绪色彩时 label 取 neutral，不要强行判正向或负向；
4. 信息不足时降低 confidence，不要过度推断用户处境。"""

STAGE2_POSITIVE_PROMPT = """你是流水线第 2 步的「鼓励者」。用户当前情绪偏正向，你的任务是放大积极面，并给出可执行的下一步。

写作要求：
1. 先具体回应：引用用户提到的具体人、事或细节，不要空泛地说"你真棒"；
2. 再点出价值：说清这件事、这个状态为什么值得肯定；
3. 最后给方向：一条具体、今天就能做的小行动；
4. 全文 120~180 字，语气真诚、口语化，不用"亲""宝子"之类套话，不说教、不喊口号；
5. tips 字段给 2~3 条不超过 20 字的行动建议。"""

STAGE2_NEGATIVE_PROMPT = """你是流水线第 2 步的「排解者」。用户当前情绪偏负面，你的任务是先接住情绪，再帮他把情绪变小、变具体。

写作要求：
1. 共情命名：用一句话准确说出他的情绪（如"听起来你是又委屈又无力"），不要用"我理解你"敷衍；
2. 正常化：解释这种反应在类似处境下很常见，减少羞耻感，但不否定他的感受；
3. 具体化：用一个简短提问或视角，帮他把模糊的难受拆成可处理的部分；
4. 低门槛行动：给一个 1~5 分钟内能做完的调节动作，如 4-7-8 呼吸三轮、写三行情绪日记、离开屏幕走五分钟；
5. 全文 120~200 字，语气平稳，不亢奋、不灌鸡汤；不做心理诊断，不承诺"一定会好起来"；
6. 若文本出现自伤、自杀或伤害他人的意图，先表达关心，明确建议立刻联系身边可信任的人，并给出求助渠道：全国心理援助热线 12356、北京心理危机干预中心 010-82951332，紧急情况拨打 120 / 110；
7. tips 字段给 2~3 条不超过 20 字的行动建议。"""

STAGE2_NEUTRAL_PROMPT = """你是流水线第 2 步的「回应者」。用户文本情绪中性，不需要刻意鼓励或安慰。

写作要求：
1. 简洁确认用户正在说的事，必要时补充一条对他有用的信息或判断；
2. 120 字以内，不硬煽情、不强行共情；
3. tips 字段给 1~2 条可执行建议，若确实没有可给空列表。"""

# ---------------------------------------------------------------- 客户端与 Agent

_client_ready = False


def _ensure_client() -> None:
    """延迟初始化全局 OpenAI 客户端，兼容第三方 OpenAI 兼容端点。"""
    global _client_ready
    if _client_ready:
        return

    settings = get_settings()
    if not settings.api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY")

    client = AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.request_timeout,
        max_retries=2,
    )
    # 第三方端点（DashScope / Ark 等）走 chat completions 协议
    set_default_openai_client(client, use_for_tracing=False)
    set_default_openai_api("chat_completions")
    # 不向 platform.openai.com 上报 trace，避免非官方 Key 报 401
    set_tracing_disabled(True)
    _client_ready = True
    logger.info("LLM 客户端就绪 model=%s base_url=%s", settings.model, settings.base_url or "官方默认")


_agents_cache: tuple[Agent, Agent, Agent, Agent] | None = None


def _get_agents() -> tuple[Agent, Agent, Agent, Agent]:
    """Agent 定义是静态的，进程内只构建一次。"""
    global _agents_cache
    if _agents_cache is not None:
        return _agents_cache

    settings = get_settings()
    common = {"model": settings.model}
    sentiment = Agent(
        name="sentiment-analyzer",
        instructions=STAGE1_PROMPT,
        output_type=SentimentResult,
        **common,
    )
    encourage = Agent(
        name="encourager",
        instructions=STAGE2_POSITIVE_PROMPT,
        output_type=SupportReply,
        **common,
    )
    comfort = Agent(
        name="comforter",
        instructions=STAGE2_NEGATIVE_PROMPT,
        output_type=SupportReply,
        **common,
    )
    neutral = Agent(
        name="responder",
        instructions=STAGE2_NEUTRAL_PROMPT,
        output_type=SupportReply,
        **common,
    )
    _agents_cache = (sentiment, encourage, comfort, neutral)
    return _agents_cache


def _route(label: str) -> tuple[Agent, str]:
    """进程 2 的分支路由。"""
    _, encourage, comfort, neutral = _get_agents()
    if label == "positive":
        return encourage, "encourage"
    if label == "negative":
        return comfort, "relief"
    return neutral, "neutral"


def _stage2_input(text: str, sentiment: SentimentResult) -> str:
    emotions = "、".join(sentiment.emotions) if sentiment.emotions else "未明确"
    return (
        f"用户原话：{text}\n"
        f"情感识别结果：{sentiment.label}（置信度 {sentiment.confidence:.2f}，"
        f"强度 {sentiment.intensity}/5），主要情绪：{emotions}，依据：{sentiment.reason}"
    )


# ---------------------------------------------------------------- 降级规则引擎（无 Key / 演示）

_NEG_WORDS = (
    "难过", "崩溃", "焦虑", "压力", "烦", "累", "累死", "哭", "失恋", "被骂", "骂", "失败",
    "委屈", "孤独", "害怕", "失眠", "内耗", "绝望", "无助", "生气", "愤怒", "讨厌", "糟糕",
    "扛不住", "不想干", "辞职", "分手", "吵架", "难受", "心累", "emo", "紧张", "延期", "加班", "被裁", "背锅",
)
_POS_WORDS = (
    "开心", "高兴", "顺利", "成功", "升职", "拿到", "通过", "喜欢", "谢谢", "感动", "幸运",
    "惊喜", "期待", "满足", "放松", "棒", "赞", "进步", "offer", "上岸", "赢", "过了", "努力", "搞定", "完成", "值了",
)
_NEGATORS = ("不", "没", "没有", "别", "不是")


def _mock_sentiment(text: str) -> SentimentResult:
    pos = sum(text.count(w) for w in _POS_WORDS)
    neg = sum(text.count(w) for w in _NEG_WORDS)
    label: str
    if neg > pos:
        label = "negative"
    elif pos > neg:
        label = "positive"
    else:
        label = "neutral"
    total = max(pos + neg, 1)
    confidence = round(min(0.55 + abs(pos - neg) / (total * 2), 0.95), 2)
    intensity = min(5, max(1, 1 + (pos + neg)))
    emotions = []
    for word in _NEG_WORDS + _POS_WORDS:
        if word in text and word not in emotions:
            emotions.append(word)
        if len(emotions) == 3:
            break
    reason = f"规则匹配：正向词 {pos} 个、负向词 {neg} 个" + ("，含否定表达需结合语境" if any(n in text for n in _NEGATORS) else "")
    return SentimentResult(
        label=label,  # type: ignore[arg-type]
        confidence=confidence,
        intensity=intensity,
        emotions=emotions or ["未明确"],
        reason=reason,
    )


def _mock_reply(text: str, sentiment: SentimentResult) -> tuple[str, list[str], str]:
    snippet = re.sub(r"\s+", " ", text.strip())[:26]
    if sentiment.label == "positive":
        return (
            f"从「{snippet}」里能感觉到你是真的在往上走，这种踏实的高兴不是运气，是你前面那些准备攒出来的。"
            "值得停一下，把它记下来：以后遇到卡壳的日子，你会需要这份证据。接下来别急着开新战场，"
            "先把这份状态的余温用在一件小事上，比如把今天让你开心的那一刻写进备忘录。",
            ["记录今天的高光时刻", "把经验拆成可复用三步"],
            "encourage",
        )
    if sentiment.label == "negative":
        return (
            f"听到「{snippet}」，你在硬扛。这种时候觉得难受、提不起劲，不是你脆弱，是这段时间的消耗确实超过了你现在能承受的量。"
            "先不用急着分析对错，试着说出三件具体让你不舒服的小事，把一团模糊的情绪拆开。"
            "现在可以做一件很小的事：把手离开键盘，做三轮 4 秒吸气、7 秒屏息、8 秒呼气的呼吸。"
            "如果这种状态持续两周以上，建议找人聊聊；若出现伤害自己的念头，请立刻联系身边可信任的人，或拨打心理援助热线 12356。",
            ["做三轮 4-7-8 呼吸", "写三行情绪日记", "找一个人说出今天的事"],
            "relief",
        )
    return (
        f"收到，「{snippet}」这件事我记下了。从描述看更像是一件待处理的具体事务，先把它拆成下一步动作会比反复想更有效。"
        "如果你愿意，可以补充背景，我再帮你看得更细。",
        ["把待办拆成一步动作"],
        "neutral",
    )


# ---------------------------------------------------------------- 主流程


async def run_pipeline(text: str, session_id: str | None = None) -> ChatResponse:
    settings = get_settings()
    started = time.perf_counter()
    trace: list[StageTrace] = []

    # 进程 1：情感识别
    t1 = time.perf_counter()
    if settings.mock:
        sentiment = _mock_sentiment(text)
        trace.append(
            StageTrace(
                stage="stage1",
                name="情感识别（内置规则引擎）",
                elapsed_ms=int((time.perf_counter() - t1) * 1000),
                detail=f"{sentiment.label} / 置信度 {sentiment.confidence}",
            )
        )
        reply, tips, tone = _mock_reply(text, sentiment)
        trace.append(
            StageTrace(
                stage="stage2",
                name=f"分支回应（{tone}）",
                elapsed_ms=0,
                detail="未配置 OPENAI_API_KEY，使用模板回复",
            )
        )
    else:
        _ensure_client()
        sentiment_agent, _, _, _ = _get_agents()
        result1 = await Runner.run(sentiment_agent, input=text, max_turns=2)
        sentiment = result1.final_output_as(SentimentResult)
        trace.append(
            StageTrace(
                stage="stage1",
                name="情感识别 Agent",
                elapsed_ms=int((time.perf_counter() - t1) * 1000),
                detail=f"{sentiment.label} / 置信度 {sentiment.confidence} / 强度 {sentiment.intensity}",
            )
        )

        # 进程 2：按识别结果分支
        t2 = time.perf_counter()
        agent, tone = _route(sentiment.label)
        result2 = await Runner.run(agent, input=_stage2_input(text, sentiment), max_turns=2)
        draft = result2.final_output_as(SupportReply)
        reply, tips = draft.reply.strip(), [t.strip() for t in draft.tips if t.strip()][:3]
        trace.append(
            StageTrace(
                stage="stage2",
                name=f"{agent.name}（{tone}）",
                elapsed_ms=int((time.perf_counter() - t2) * 1000),
                detail=f"输出 {len(reply)} 字，{len(tips)} 条建议",
            )
        )

    return ChatResponse(
        message=text,
        sentiment=sentiment,
        reply=reply,
        tone=tone,  # type: ignore[arg-type]
        tips=tips,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        trace=trace,
    )

