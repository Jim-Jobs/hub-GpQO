"""
编排层（Orchestration Layer）—— 后端 B：OpenAI-Agents 服务

不使用状态图，也不使用 Agent Handoff。
用普通同步函数 + Python 原生 if-else 完成两段式编排：

  Runner.run_sync(sentiment_agent)          ← 进程 1（识别）
      │
      ▼
  【条件路由】final_output.sentiment
      ├── positive ──► Runner.run_sync(encourage_agent)   ← 进程 2（生成 · 正向）
      └── negative ──► Runner.run_sync(console_agent)     ← 进程 2（生成 · 负向）
      │
      ▼
     ChatResponse
"""

from agents import Runner

from local_agents import console_agent, encourage_agent, sentiment_agent
from schemas import ChatResponse


def run_workflow(user_input: str) -> ChatResponse:
    """
    同步执行完整流程（不使用 async/await）。

    :param user_input: 用户输入文本
    :return: ChatResponse(sentiment, response)
    """
    # ---- 进程 1（识别）：情感分类，拿到结构化结果 SentimentOutput ----
    sentiment_result = Runner.run_sync(sentiment_agent, user_input)
    final_output = sentiment_result.final_output
    sentiment = final_output.sentiment

    # ---- 【条件路由】：原生 if-else 选择生成 Agent ----
    if sentiment == "positive":
        # 进程 2（生成 · 正向）
        agent_result = Runner.run_sync(encourage_agent, user_input)
    else:
        # 进程 2（生成 · 负向）
        agent_result = Runner.run_sync(console_agent, user_input)

    response = str(agent_result.final_output).strip()

    return ChatResponse(sentiment=sentiment, response=response)
