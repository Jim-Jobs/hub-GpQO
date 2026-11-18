"""按工作进程边界构建两张独立的 LangGraph 状态图。"""

from typing import NotRequired, TypedDict

from backend.agent import classify_sentiment, generate_reply


class SentimentState(TypedDict):
    """情感识别图的输入与节点结果。"""

    request_id: str
    text: str
    sentiment: NotRequired[str]
    reason: NotRequired[str]


class ReplyState(TypedDict):
    """情绪回应图的输入与节点结果。"""

    request_id: str
    text: str
    sentiment: str
    reason: str
    reply: NotRequired[str]


def build_sentiment_workflow():
    """构建情感识别工作进程使用的图。

    Args:
        无。

    Returns:
        从 START 运行识别 Agent 节点并结束的编译图。

    Raises:
        ImportError: LangGraph 运行依赖未安装。
    """
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(SentimentState)
    graph.add_node("identify_sentiment", classify_sentiment)
    graph.add_edge(START, "identify_sentiment")
    graph.add_edge("identify_sentiment", END)
    return graph.compile()


def build_reply_workflow():
    """构建情绪回应工作进程使用的图。

    Args:
        无。

    Returns:
        从 START 运行回复 Agent 节点并结束的编译图。

    Raises:
        ImportError: LangGraph 运行依赖未安装。
    """
    from langgraph.graph import END, START, StateGraph

    graph = StateGraph(ReplyState)
    graph.add_node("generate_reply", generate_reply)
    graph.add_edge(START, "generate_reply")
    graph.add_edge("generate_reply", END)
    return graph.compile()
