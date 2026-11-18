"""StateGraph：START → sentiment → respond → END。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from langgraph_impl.nodes import GraphState, respond_node, sentiment_node


def build_graph():
    """构建并编译情感识别 → 回应 的线性图。"""
    builder = StateGraph(GraphState)
    builder.add_node("sentiment", sentiment_node)
    builder.add_node("respond", respond_node)
    builder.add_edge(START, "sentiment")
    builder.add_edge("sentiment", "respond")
    builder.add_edge("respond", END)
    return builder.compile()


# 模块级单例，供 server 直接调用
sentiment_graph = build_graph()


def run_pipeline(text: str) -> GraphState:
    """后端编排入口：串联进程 1 与进程 2。"""
    return sentiment_graph.invoke({"text": text})
