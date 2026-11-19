"""作业 1 的 LangGraph 实现。

用显式状态图表达「进程 1 → 条件路由 → 进程 2」：

    START → analyze ─┬─(positive)─→ encourage ─┐
                     ├─(negative)─→ comfort   ─┼→ END
                     ├─(neutral) ─→ neutral   ─┤
                     └─(crisis)  ─→ crisis    ─┘

路由逻辑是普通 Python 函数 ``_route``，可以脱离模型单独做单元测试。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from app.engines.base import Engine, EngineResult
from app.mock import MockBrain, detect_crisis
from app.prompts import (
    COMFORT_PROMPT,
    CRISIS_PROMPT,
    ENCOURAGE_PROMPT,
    NEUTRAL_PROMPT,
    SENTIMENT_PROMPT,
)
from app.schemas import Sentiment, SentimentLabel


class GraphState(TypedDict, total=False):
    text: str
    sentiment: Sentiment
    reply: str
    crisis: bool
    trail: Annotated[list[str], operator.add]


class LangGraphEngine(Engine):
    name = "langgraph"

    def __init__(self, model=None, brain: MockBrain | None = None) -> None:
        self.model = model
        self.brain = brain
        self.graph = self._build()

    # ---------- 进程 1 ----------
    async def _analyze(self, state: GraphState) -> dict:
        text = state["text"]
        crisis = detect_crisis(text)
        if self.brain is not None:
            sentiment = self.brain.analyze(text)
        else:
            chain = self.model.with_structured_output(Sentiment)
            sentiment = await chain.ainvoke(
                [SystemMessage(content=SENTIMENT_PROMPT), HumanMessage(content=text)]
            )
        return {"sentiment": sentiment, "crisis": crisis, "trail": ["analyze"]}

    # ---------- 路由（条件边） ----------
    @staticmethod
    def _route(state: GraphState) -> str:
        if state.get("crisis"):
            return "crisis"
        label = state["sentiment"].label
        return {
            SentimentLabel.POSITIVE: "encourage",
            SentimentLabel.NEGATIVE: "comfort",
        }.get(label, "neutral")

    # ---------- 进程 2 ----------
    def _reply_node(self, node_name: str, system_prompt: str):
        async def node(state: GraphState) -> dict:
            sentiment = state["sentiment"]
            if self.brain is not None:
                reply = self.brain.reply(state["text"], sentiment, crisis=state.get("crisis", False))
            else:
                human = (
                    f"用户原话：{state['text']}\n"
                    f"情绪判断：{sentiment.label.value}\n"
                    f"判断依据：{sentiment.reason}"
                )
                message = await self.model.ainvoke(
                    [SystemMessage(content=system_prompt), HumanMessage(content=human)]
                )
                reply = str(message.content)
            return {"reply": reply.strip(), "trail": [node_name]}

        return node

    def _build(self):
        graph = StateGraph(GraphState)
        graph.add_node("analyze", self._analyze)
        graph.add_node("encourage", self._reply_node("encourage", ENCOURAGE_PROMPT))
        graph.add_node("comfort", self._reply_node("comfort", COMFORT_PROMPT))
        graph.add_node("neutral", self._reply_node("neutral", NEUTRAL_PROMPT))
        graph.add_node("crisis", self._reply_node("crisis", CRISIS_PROMPT))

        graph.add_edge(START, "analyze")
        graph.add_conditional_edges(
            "analyze",
            self._route,
            {
                "encourage": "encourage",
                "comfort": "comfort",
                "neutral": "neutral",
                "crisis": "crisis",
            },
        )
        for node in ("encourage", "comfort", "neutral", "crisis"):
            graph.add_edge(node, END)
        return graph.compile()

    async def run(self, text: str) -> EngineResult:
        return await self._safe_run(text, self._run)

    async def _run(self, text: str) -> EngineResult:
        final = await self.graph.ainvoke({"text": text, "trail": []})
        return EngineResult(
            sentiment=final["sentiment"],
            reply=final["reply"],
            trail=final.get("trail", []),
            mode="mock" if self.brain is not None else "live",
            crisis=final.get("crisis", False),
        )

    def mermaid(self) -> str:
        """给 README / 答辩 PPT 用的架构图（无需把图渲染成图片）。"""
        return self.graph.get_graph().draw_mermaid()
