"""
编排层（Orchestration Layer）—— 后端 A：LangGraph 服务

用 LangGraph 的 StateGraph 把三个节点串起来：

    START
      │
      ▼
  analyze_sentiment          ← 进程 1（识别）
      │
      ▼
  「条件路由」：读取 state["sentiment"]  ← 条件路由位置
      ├── positive ──► generate_encouragement   ← 进程 2（生成 · 正向）
      └── negative ──► generate_console         ← 进程 2（生成 · 负向）
                          │
                          ▼
                        END

编译后导出全局变量 app_graph，供 main.py 的 /chat 接口调用。
"""

from langgraph.graph import END, START, StateGraph

from nodes import analyze_sentiment, generate_console, generate_encouragement
from schemas import AgentState


def route_by_sentiment(state: AgentState) -> str:
    """
    【条件路由】根据识别节点产出的 sentiment 决定走哪个生成节点。

    返回的是条件边映射表中的 key。
    """
    return "positive" if state.get("sentiment") == "positive" else "negative"


def build_graph() -> StateGraph:
    """构建并编译状态图。"""
    graph = StateGraph(AgentState)

    # 添加三个节点
    graph.add_node("analyze_sentiment", analyze_sentiment)          # 进程 1（识别）
    graph.add_node("generate_encouragement", generate_encouragement)  # 进程 2（生成 · 正向）
    graph.add_node("generate_console", generate_console)              # 进程 2（生成 · 负向）

    # 入口：先做情感识别
    graph.add_edge(START, "analyze_sentiment")

    # 【条件路由位置】读取 sentiment，positive 走鼓励，negative 走排解
    graph.add_conditional_edges(
        "analyze_sentiment",
        route_by_sentiment,
        {
            "positive": "generate_encouragement",
            "negative": "generate_console",
        },
    )

    # 两个生成节点执行完后直接结束
    graph.add_edge("generate_encouragement", END)
    graph.add_edge("generate_console", END)

    return graph.compile()


# 全局单例，main.py 直接复用
app_graph = build_graph()
