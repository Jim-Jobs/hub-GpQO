# CONTEXT

项目术语表。只收录本项目有歧义或需规范化的术语，不放实现细节。

| 术语 | 规范写法 | 定义 | 易混淆点 |
| --- | --- | --- | --- |
| 会话 | Conversation / `conversation_id` | 业务概念，对应 `conversations` 表；ID 由前端生成，UUID v4 格式，后端首次见到时自动建档 | 不等于引擎的 thread / session |
| 业务历史 | Business History | `messages` 表中的用户 / 助手消息，前端历史展示的**唯一真相来源** | 与引擎状态互不读取 |
| 引擎状态 | Engine State | 模型多轮上下文，存于各引擎自己的状态表（LangGraph checkpointer 表 / Agents session 表）；`thread_id` 与 `session_id` 均等于 `conversation_id` | 切换引擎后丢失，业务历史不受影响 |
| 引擎 | Engine | 可切换的编排实现，合法取值仅 `langgraph` 与 `openai-agents`，由 `PIPELINE_ENGINE` 选择；非法取值启动即失败 | 不称「管线」；「管线 / pipeline」仅出现在 `PipelineAdapter` 接口名中 |
| PipelineAdapter | PipelineAdapter | 引擎的统一接口，`domain.ports` 定义，两套引擎各自实现；不是独立实体 | 不要用它指代引擎本身 |
| 情感 | Sentiment | 用户输入的二分类结果，仅 `positive` / `negative`，不含置信度与理由 | 不称「情绪」「情绪分类」 |
| 游标 | Cursor / `before` | 历史分页参数，取值为消息 ID，对前端是不透明游标；后端内部按 (created_at, id) 定位 | 不以时间戳作为游标 |
| 输入文本 | Input | 用户消息 trim 后的文本；校验、存储、回显统一使用它 | 不保留未 trim 原文 |
