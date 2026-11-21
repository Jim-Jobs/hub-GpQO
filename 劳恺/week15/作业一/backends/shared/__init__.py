"""两套 Agent 框架实现共用的领域模型、基础设施与质量校验逻辑。

本包内只包含**框架无关**的业务逻辑（Schema、提示词、解析、质量检查、数据库、
Agent HTTP 客户端、编排器应用工厂）。LangGraph 与 OpenAI Agents SDK 的
**Agent 实现**分别位于 ``backends.langgraph`` 与 ``backends.openai_agents``，
两者互不引用。
"""
