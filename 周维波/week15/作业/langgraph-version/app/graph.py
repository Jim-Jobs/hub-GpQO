"""LangGraph 版流水线：把两个进程显式建成图的节点，用条件边做分支。

    START → analyze(进程1) → [条件路由] → encourage / comfort / neutral(进程2) → END
"""

from __future__ import annotations

import logging
import re
import time
from typing import TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

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


# ---------------------------------------------------------------- 状态定义


class PipelineState(TypedDict, total=False):
    """在各节点间流转的状态；节点只返回自己改动的字段。"""

    user_input: str
    sentiment: SentimentResult
    reply: str
    tips: list[str]
    tone: str
    trace: list[StageTrace]


# ---------------------------------------------------------------- 构图

_graph = None
_structured_sentiment = None
_structured_reply = None


def _build_llm() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=settings.temperature,
        timeout=settings.request_timeout,
        max_retries=2,
    )


def _get_structured_models():
    global _structured_sentiment, _structured_reply
    if _structured_sentiment is None or _structured_reply is None:
        llm = _build_llm()
        _structured_sentiment = llm.with_structured_output(SentimentResult)
        _structured_reply = llm.with_structured_output(SupportReply)
        logger.info("LangGraph 结构化输出模型就绪 model=%s", get_settings().model)
    return _structured_sentiment, _structured_reply


def _stage2_input(text: str, sentiment: SentimentResult) -> str:
    emotions = "、".join(sentiment.emotions) if sentiment.emotions else "未明确"
    return (
        f"用户原话：{text}\n"
        f"情感识别结果：{sentiment.label}（置信度 {sentiment.confidence:.2f}，"
        f"强度 {sentiment.intensity}/5），主要情绪：{emotions}，依据：{sentiment.reason}"
    )


def _append_trace(state: PipelineState, item: StageTrace) -> list[StageTrace]:
    return list(state.get("trace", [])) + [item]


async def analyze_node(state: PipelineState) -> dict:
    """进程 1：情感识别。"""
    started = time.perf_counter()
    structured_sentiment, _ = _get_structured_models()
    result = await structured_sentiment.ainvoke(
        [SystemMessage(content=STAGE1_PROMPT), HumanMessage(content=state["user_input"])]
    )
    if not isinstance(result, SentimentResult):  # 兜底，避免第三方模型返回裸 dict
        result = SentimentResult.model_validate(result)

    trace_item = StageTrace(
        stage="stage1",
        name="情感识别节点",
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        detail=f"{result.label} / 置信度 {result.confidence} / 强度 {result.intensity}",
    )
    return {"sentiment": result, "trace": _append_trace(state, trace_item)}


def route_by_sentiment(state: PipelineState) -> str:
    """条件边：按 label 决定进程 2 走哪个分支。"""
    return state["sentiment"].label


def _reply_node(prompt: str, tone: str, name: str):
    """生成进程 2 的节点；三个分支共用同一套逻辑，只是提示词与语气不同。"""

    async def node(state: PipelineState) -> dict:
        started = time.perf_counter()
        _, structured_reply = _get_structured_models()
        draft = await structured_reply.ainvoke(
            [
                SystemMessage(content=prompt),
                HumanMessage(content=_stage2_input(state["user_input"], state["sentiment"])),
            ]
        )
        if not isinstance(draft, SupportReply):
            draft = SupportReply.model_validate(draft)

        reply = draft.reply.strip()
        tips = [t.strip() for t in draft.tips if t.strip()][:3]
        trace_item = StageTrace(
            stage="stage2",
            name=f"{name}（{tone}）",
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            detail=f"输出 {len(reply)} 字，{len(tips)} 条建议",
        )
        return {"reply": reply, "tips": tips, "tone": tone, "trace": _append_trace(state, trace_item)}

    return node


def build_graph():
    builder = StateGraph(PipelineState)
    builder.add_node("analyze", analyze_node)
    builder.add_node("encourage", _reply_node(STAGE2_POSITIVE_PROMPT, "encourage", "鼓励分支"))
    builder.add_node("comfort", _reply_node(STAGE2_NEGATIVE_PROMPT, "relief", "排解分支"))
    builder.add_node("neutral", _reply_node(STAGE2_NEUTRAL_PROMPT, "neutral", "中性分支"))

    builder.add_edge(START, "analyze")
    builder.add_conditional_edges(
        "analyze",
        route_by_sentiment,
        {"positive": "encourage", "negative": "comfort", "neutral": "neutral"},
    )
    builder.add_edge("encourage", END)
    builder.add_edge("comfort", END)
    builder.add_edge("neutral", END)
    return builder.compile()


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


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
    label = "negative" if neg > pos else "positive" if pos > neg else "neutral"
    total = max(pos + neg, 1)
    confidence = round(min(0.55 + abs(pos - neg) / (total * 2), 0.95), 2)
    intensity = min(5, max(1, 1 + (pos + neg)))
    emotions: list[str] = []
    for word in _NEG_WORDS + _POS_WORDS:
        if word in text and word not in emotions:
            emotions.append(word)
        if len(emotions) == 3:
            break
    reason = f"规则匹配：正向词 {pos} 个、负向词 {neg} 个" + (
        "，含否定表达需结合语境" if any(n in text for n in _NEGATORS) else ""
    )
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


# ---------------------------------------------------------------- 对外入口


async def run_pipeline(text: str, session_id: str | None = None) -> ChatResponse:
    settings = get_settings()
    started = time.perf_counter()

    if settings.mock:
        t1 = time.perf_counter()
        sentiment = _mock_sentiment(text)
        trace = [
            StageTrace(
                stage="stage1",
                name="情感识别（内置规则引擎）",
                elapsed_ms=int((time.perf_counter() - t1) * 1000),
                detail=f"{sentiment.label} / 置信度 {sentiment.confidence}",
            )
        ]
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
        graph = get_graph()
        final_state = await graph.ainvoke(
            {"user_input": text, "trace": []},
            config={"recursion_limit": 10, "configurable": {"thread_id": session_id or "default"}},
        )
        sentiment = final_state["sentiment"]
        reply = final_state["reply"]
        tips = final_state.get("tips", [])
        tone = final_state["tone"]
        trace = final_state["trace"]

    return ChatResponse(
        message=text,
        sentiment=sentiment,
        reply=reply,
        tone=tone,  # type: ignore[arg-type]
        tips=tips,
        elapsed_ms=int((time.perf_counter() - started) * 1000),
        trace=trace,
    )

