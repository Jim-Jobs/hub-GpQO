# ChatBI / Data Agent — 技术设计

> 本文档在功能设计基础上，描述系统「如何实现、架构如何分层、技术如何选型」。

---

## 1. 整体架构

### 1.1 分层架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                          客户端层（Client）                          │
│   Web 聊天界面（React / Vue SPA）                                    │
│   - 对话输入 / 消息渲染 / 表格展示 / ECharts 图表 / 溯源面板         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │  HTTP / WebSocket（流式）
┌──────────────────────────────▼──────────────────────────────────────┐
│                         接口层（API Layer）                          │
│   FastAPI（Python 3.12）                                             │
│   - /chat（问数主接口）/ /session（会话管理）/ /datasource（数据源） │
│   - /schema（语义层管理）/ /export（结果导出）                        │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────────┐
│                       Agent 编排层（Orchestration）                   │
│   Agent Runtime（LangGraph / 自研循环）                              │
│   - IntentAgent（意图识别）                                          │
│   - SchemaAgent（语义层检索 + 数据源路由）                            │
│   - SQLAgent（SQL 生成 + 校验 + 重试）                               │
│   - AnalysisAgent（结果解读 + 图表推荐）                             │
│   - ResearchAgent（复杂问题拆解 + 多步循环 + 洞察汇总）              │
└──────────┬───────────────────────────────────────┬──────────────────┘
           │                                       │
┌──────────▼──────────────────┐    ┌──────────────▼──────────────────┐
│    语义层（Semantic Layer）  │    │       模型适配层（LLM Adapter）  │
│  Embedding + VectorStore     │    │  统一接口 → DeepSeek / OpenAI   │
│  - SchemaIndex（表/列/关系） │    │  / Claude / 本地模型（Ollama）  │
│  - GlossaryIndex（术语字典） │    │  统一 Chat / Tool-Call 接口     │
│  - FewShotIndex（历史问答）  │    │                                 │
└──────────┬──────────────────┘    └──────────────┬──────────────────┘
           │                                       │
┌──────────▼───────────────────────────────────────▼─────────────────┐
│                     数据访问层（Data Access Layer）                  │
│   DataSource 抽象接口（连接池 / 执行 / Schema 探查）                 │
│   ├── SQLiteConnector   （文件路径，零配置）                        │
│   ├── MySQLConnector    （连接串 + 连接池）                         │
│   ├── PostgresConnector （连接串 + 连接池）                         │
│   └── RestApiConnector  （预留：对接数据接口）                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 各层职责说明

| 层次 | 职责 | 关键设计原则 |
|---|---|---|
| 客户端层 | 用户交互、数据/图表渲染 | 与后端协议对齐，流式展示 Agent 思考过程 |
| 接口层 | HTTP/WS 端点、请求路由、鉴权（预留） | RESTful + SSE，薄层不含业务逻辑 |
| Agent 编排层 | Agent 循环控制、Prompt 组装、工具调用决策 | 各 Agent 职责单一，通过消息传递协同 |
| 语义层 | Schema / 术语 / 历史问答的向量存储与检索 | 数据源无关，支持增量更新 |
| 模型适配层 | 屏蔽不同 LLM 厂商接口差异 | 对齐 OpenAI Tool-Call 协议，热切换 |
| 数据访问层 | 多数据源统一接入、SQL 执行、结果封装 | 连接池管理，安全护栏内置 |

---

## 2. 技术选型

### 2.1 选型总表

| 组件 | 技术选择 | 理由 |
|---|---|---|
| 后端框架 | **FastAPI** | 异步支持好，自带 OpenAPI 文档，适合流式 SSE 输出 |
| Web 前端 | **React + Vite** | 生态丰富，ECharts 封装成熟，组件化开发 |
| 图表渲染 | **ECharts** | 支持服务端 JSON Spec 直接渲染，轻量 |
| Agent 框架 | **LangGraph**（或自研） | 状态机式多 Agent 循环，支持分支/重试/人工干预节点 |
| LLM 适配 | **litellm**（Python 库） | 一套接口调用 100+ 模型，快速切换，无需各自封装 |
| Embedding | **text2vec / jina-embeddings**（可切换） | 中文效果好，可本地运行，减少 API 依赖 |
| 向量数据库 | **ChromaDB**（轻量）/ Milvus（生产） | 本地文件存储起步，无需单独起服务 |
| 数据库驱动 | **SQLAlchemy**（通用 ORM） | 多数据库方言支持，统一 Schema 探查接口 |
| 会话存储 | **SQLite**（起步）/ Redis（生产） | 起步不依赖外部中间件 |
| 异步任务 | **asyncio**（内置）+ FastAPI BackgroundTask | 轻量，不引入 Celery 等重型队列 |
| 包管理 | **uv** / Poetry | Python 依赖锁定，可复现环境 |

### 2.2 LLM 选型（模型无关设计）

模型适配层通过 **litellm** 统一封装，调用方只需切换配置：

```python
# 配置示例（通过环境变量或 config.yaml 切换，无需改代码）
LLM_PROVIDER=deepseek
LLM_MODEL=deepseek-v4-flash
LLM_BASE_URL=https://api.deepseek.com/v1

# 切换为 OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_BASE_URL=https://api.openai.com/v1

# 切换为本地 Ollama
LLM_PROVIDER=ollama
LLM_MODEL=llama3
LLM_BASE_URL=http://localhost:11434
```

---

## 3. Agent 设计

### 3.1 Agent 角色划分

```
用户问题进入
      ↓
[IntentAgent]
  - 分类：取数 / 分析 / 操作 / 闲聊
  - 判断是否需要拆解（标记 is_complex）
  - 输出：intent + is_complex + entities（时间/维度/指标）
      ↓
  ├── is_complex=False → 走「基础问数链」
  │       ↓
  │   [SchemaAgent]
  │     - RAG 检索相关表/字段/术语/Few-shot
  │     - 确定目标数据源
  │     - 输出：schema_context + datasource_id
  │       ↓
  │   [SQLAgent]
  │     - 组装 Prompt → 调用 LLM → 生成 SQL
  │     - 安全校验（只读/表列合法/LIMIT）
  │     - 校验失败 / 执行报错 → 携带错误信息重新调用 LLM 修正
  │     - 输出：sql + result_set
  │       ↓
  │   [AnalysisAgent]
  │     - 结果解读（自然语言总结）
  │     - 图表推荐（生成 ECharts Spec JSON）
  │     - 输出：summary + chart_spec + table_data
  │
  └── is_complex=True → 走「深度分析链」
          ↓
      [ResearchAgent]
        - 问题拆解（输出子问题 DAG）
        - 按依赖顺序调度「基础问数链」执行子问题
        - 每步反思：结果是否充分？是否需补充查询？
        - 最终汇总：调用 LLM 生成洞察报告（Markdown）
        - 输出：executed_steps + insights + report
```

### 3.2 Agent 状态机（LangGraph 风格）

```python
# 状态定义（伪代码）
class ChatBIState(TypedDict):
    messages: list          # 对话历史
    question: str           # 当前问题
    intent: str             # 意图标签
    is_complex: bool        # 是否复杂问题
    schema_context: str     # RAG 召回的 Schema 片段
    datasource_id: str      # 目标数据源
    generated_sql: str      # 当前生成的 SQL
    sql_error: str          # 执行错误信息（用于重试）
    retry_count: int        # 已重试次数
    result_set: list        # 查询结果
    chart_spec: dict        # ECharts 图表配置
    summary: str            # 自然语言结论
    sub_questions: list     # 复杂问题子任务列表
    step_results: dict      # 各子步骤结果缓存
    report: str             # 最终洞察报告
```

### 3.3 SQLAgent 重试机制

```python
MAX_RETRY = 3

def generate_and_execute(question, schema_ctx, datasource):
    for attempt in range(MAX_RETRY):
        sql = call_llm(build_prompt(question, schema_ctx, error_so_far))
        sql = safety_check(sql)        # 静态校验，不合法直接改写
        if sql is None:
            error_so_far = "SQL 安全校验失败，请重新生成只读查询"
            continue
        try:
            result = datasource.execute(sql, timeout=15, max_rows=1000)
            return sql, result          # 成功，退出
        except SQLError as e:
            error_so_far = str(e)       # 将 DB 报错注入下一轮 Prompt
            continue
    raise UnableToGenerateSQL(question)  # 超出重试上限
```

---

## 4. RAG 语义层设计

### 4.1 索引构建流程

```
数据源接入
      ↓
[Schema 探查] → 读取表结构、列信息、主外键关系
      ↓
[注释增强] → 从 information_schema 读取已有注释 / 管理员手动补充
      ↓
[向量化] → 对每个语义单元（表级/列级/关系级/术语/历史问答）调用 Embedding
      ↓
[写入 VectorStore] → 按 datasource_id 做命名空间隔离
```

### 4.2 检索策略（Hybrid Search）

```python
def retrieve_schema_context(question, datasource_id, top_k=8):
    # 1. 语义向量检索（召回最相关的表/列）
    semantic_hits = vector_store.search(
        query=embed(question),
        namespace=datasource_id,
        top_k=top_k
    )

    # 2. 关键词精确匹配（补充向量检索漏掉的字段）
    keyword_hits = vector_store.keyword_search(
        keywords=extract_keywords(question),
        namespace=datasource_id,
        top_k=4
    )

    # 3. 合并去重，按相关度排序
    candidates = dedupe(semantic_hits + keyword_hits)

    # 4. 扩展关联表（外键关联的表自动补上）
    enriched = expand_foreign_keys(candidates, datasource_id)

    return build_schema_prompt(enriched)
```

### 4.3 Few-shot 历史问答检索

每次 SQLAgent 生成前，从 FewShotIndex 检索最相似的历史问答：

```python
few_shots = fewshot_store.search(
    query=embed(question),
    top_k=3,
    filter={"result_verified": True}   # 只用验证过的问答
)
# 注入 Prompt 中作为示例，提升准确率
```

历史问答来源：

- 系统管理员手动录入标准问答对。
- 自动挖掘：用户点赞某次回答后，该问答对入候选队列，经审核后写入。

---

## 5. 数据源抽象层设计

### 5.1 核心接口

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    comment: str | None

@dataclass
class TableInfo:
    name: str
    comment: str | None
    columns: list[ColumnInfo]
    primary_key: list[str]
    foreign_keys: list[dict]   # {"column": str, "ref_table": str, "ref_column": str}

@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list]
    row_count: int
    execution_ms: int

class DataSource(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def list_tables(self) -> list[TableInfo]: ...

    @abstractmethod
    def execute_readonly(self, sql: str, timeout: int = 15, max_rows: int = 1000) -> QueryResult: ...

    @abstractmethod
    def health_check(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...
```

### 5.2 SQLite 实现示例

```python
class SQLiteDataSource(DataSource):
    def __init__(self, file_path: str):
        self.file_path = file_path
        self._conn = None

    def connect(self):
        self._conn = sqlite3.connect(
            f"file:{self.file_path}?mode=ro",   # 强制只读
            uri=True,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row

    def list_tables(self) -> list[TableInfo]:
        # 通过 PRAGMA table_info / PRAGMA foreign_key_list 探查
        ...

    def execute_readonly(self, sql, timeout=15, max_rows=1000) -> QueryResult:
        # 使用 con.set_progress_handler 实现超时中断
        # SELECT 外加 LIMIT 保护
        ...
```

### 5.3 数据源注册与路由

```python
# config.yaml
datasources:
  - id: chinook
    type: sqlite
    path: ./data/chinook.db
    description: "音乐零售演示库"

  - id: company_mysql
    type: mysql
    dsn: mysql+pymysql://readonly:pass@host:3306/analytics
    description: "公司经营分析主库"
```

数据源路由由 SchemaAgent 根据问题语义 + 表名映射决定调用哪个 `datasource_id`。

---

## 6. 接口设计

### 6.1 主要端点

| 方法 | 路径 | 描述 |
|---|---|---|
| POST | `/api/v1/chat` | 主问数接口（SSE 流式返回） |
| GET | `/api/v1/sessions` | 获取历史会话列表 |
| GET | `/api/v1/sessions/{id}` | 获取某会话详情（消息历史） |
| DELETE | `/api/v1/sessions/{id}` | 删除会话 |
| GET | `/api/v1/datasources` | 列出已接入数据源 |
| POST | `/api/v1/datasources` | 注册新数据源（含权限校验） |
| POST | `/api/v1/datasources/{id}/refresh-schema` | 触发 Schema 重新探查与向量化 |
| GET | `/api/v1/glossary` | 查看术语字典 |
| POST | `/api/v1/glossary` | 录入业务术语/指标定义 |
| POST | `/api/v1/export/{query_id}` | 导出查询结果为 CSV/Excel |

### 6.2 核心接口 — `/api/v1/chat` 请求/响应

**请求体（JSON）**：

```json
{
  "session_id": "abc-123",
  "question": "各地区上月的 GMV 是多少？哪个最高？",
  "datasource_hint": "chinook",
  "history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

**SSE 流式响应事件序列**：

```
event: step
data: {"agent": "IntentAgent", "status": "done", "intent": "取数", "is_complex": false}

event: step
data: {"agent": "SchemaAgent", "status": "done", "tables_used": ["Invoice","Customer"], "datasource": "chinook"}

event: step
data: {"agent": "SQLAgent", "status": "done", "sql": "SELECT ...", "retry_count": 0}

event: result
data: {"columns": ["BillingCountry","GMV"], "rows": [["USA", 1000], ["Canada", 800], ...]}

event: chart
data: {"type": "bar", "title": "各地区 GMV", "echarts_option": {...}}

event: summary
data: {"text": "上月 GMV 最高的是 USA，总计 $1,000，比第二名 Canada 高约 25%..."}

event: done
data: {"query_id": "q-456", "elapsed_ms": 2340}
```

---

## 7. 项目目录结构

```
chatbi/
├── backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI 入口
│   │   ├── api/
│   │   │   ├── chat.py             # /chat 接口
│   │   │   ├── session.py          # 会话管理接口
│   │   │   ├── datasource.py       # 数据源管理接口
│   │   │   └── glossary.py         # 术语字典接口
│   │   ├── agents/
│   │   │   ├── intent_agent.py     # 意图识别 Agent
│   │   │   ├── schema_agent.py     # Schema 检索 Agent
│   │   │   ├── sql_agent.py        # SQL 生成/校验/重试 Agent
│   │   │   ├── analysis_agent.py   # 结果解读/图表推荐 Agent
│   │   │   └── research_agent.py   # 深度分析 Agent（多步循环）
│   │   ├── core/
│   │   │   ├── state.py            # Agent 状态定义
│   │   │   ├── graph.py            # LangGraph 状态机编排
│   │   │   └── safety.py           # SQL 安全校验规则
│   │   ├── rag/
│   │   │   ├── schema_indexer.py   # Schema 向量化索引构建
│   │   │   ├── glossary_indexer.py # 术语字典向量化
│   │   │   ├── fewshot_store.py    # 历史问答存取
│   │   │   └── retriever.py        # Hybrid 检索逻辑
│   │   ├── datasource/
│   │   │   ├── base.py             # DataSource 抽象接口
│   │   │   ├── sqlite.py           # SQLite 实现
│   │   │   ├── mysql.py            # MySQL 实现
│   │   │   └── registry.py         # 数据源注册 & 路由
│   │   ├── llm/
│   │   │   └── adapter.py          # litellm 统一调用封装
│   │   └── storage/
│   │       ├── session_store.py    # 会话持久化
│   │       └── audit_log.py        # 审计日志（预留）
│   ├── config.yaml                 # 数据源/LLM/Agent 配置
│   ├── requirements.txt / pyproject.toml
│   └── tests/
│       └── eval/                   # Text-to-SQL 评测集
│
├── frontend/
│   ├── src/
│   │   ├── pages/
│   │   │   └── Chat.tsx            # 主对话页面
│   │   ├── components/
│   │   │   ├── MessageBubble.tsx   # 消息气泡（文字+表格+图表）
│   │   │   ├── DataTable.tsx       # 可排序数据表格
│   │   │   ├── ChartRenderer.tsx   # ECharts 图表封装
│   │   │   ├── StepPanel.tsx       # Agent 思考流进度面板
│   │   │   └── TracePanel.tsx      # SQL 溯源展开面板
│   │   ├── hooks/
│   │   │   └── useSSE.ts           # SSE 流式读取 Hook
│   │   └── store/
│   │       └── session.ts          # 会话状态管理
│   ├── package.json
│   └── vite.config.ts
│
└── docs/
    ├── job2-项目设计.md
    ├── job2-功能设计.md
    └── job2-技术设计.md             # 本文
```

---

## 8. 安全设计

### 8.1 SQL 安全校验

```python
FORBIDDEN_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE",
    "ALTER", "CREATE", "GRANT", "REVOKE", "EXEC", "MERGE"
}

def validate_sql(sql: str, allowed_tables: set[str], allowed_columns: set[str]) -> tuple[bool, str]:
    # 1. 关键字黑名单
    upper = sql.upper()
    for kw in FORBIDDEN_KEYWORDS:
        if re.search(rf'\b{kw}\b', upper):
            return False, f"Forbidden keyword: {kw}"

    # 2. 只允许 SELECT / WITH 开头
    if not re.match(r'^\s*(SELECT|WITH)\b', upper):
        return False, "Only SELECT / WITH allowed"

    # 3. 表名合法性（防 LLM 幻觉引用不存在的表）
    referenced_tables = extract_table_names(sql)
    for t in referenced_tables:
        if t not in allowed_tables:
            return False, f"Unknown table: {t}"

    # 4. 强制注入 LIMIT
    if "LIMIT" not in upper:
        sql = sql.rstrip(';') + " LIMIT 1000"

    return True, sql
```

### 8.2 数据源层只读保护

- SQLite：以 `mode=ro` URI 打开，物理上禁止写入。
- MySQL/PostgreSQL：使用**只读账号**连接，数据库层面拒绝 DML/DDL。
- 查询超时：通过 `SET max_execution_time`（MySQL）或 `statement_timeout`（PG）在 DB 层兜底。

---

## 9. 评测集设计（质量保障）

建立标准评测集，每次 Prompt 调整或模型切换后运行回归测试。

```json
// eval/dataset.json
[
  {
    "id": 1,
    "datasource": "chinook",
    "question": "共有多少个客户？",
    "expected_sql": "SELECT COUNT(*) FROM Customer",
    "expected_answer_keywords": ["数量", "客户"]
  },
  {
    "id": 2,
    "datasource": "chinook",
    "question": "消费金额最高的前 5 名客户是谁？",
    "expected_sql": "SELECT CustomerId, SUM(Total) AS GMV FROM Invoice GROUP BY CustomerId ORDER BY GMV DESC LIMIT 5",
    "expected_answer_keywords": ["客户", "Top"]
  }
]
```

评测指标：

- **SQL 可执行率**：生成的 SQL 不报错的比例。
- **结果正确率**：执行结果与标准 SQL 结果一致的比例。
- **幻觉率**：引用不存在的表/列的比例。

---

## 10. Prompt 工程设计

> 在 ChatBI 场景，**Prompt 与语义层就是产品本体**。模型能力只能决定上限，Prompt 与召回质量决定下限。

### 10.1 SQLAgent 系统提示词模板

```text
你是一个严谨的 SQL 生成引擎。你的任务是将自然语言问题转换为可在 {dialect} 上执行的查询语句。

# 可用表结构（只能使用以下列出的表和列，绝对不得自行发明）
{schema_context}

# 业务指标口径（优先使用定义的公式，不要自己重新发明）
{metric_context}

# 参考示例（已验证的历史问答）
{few_shot_examples}

# 必须遵守的规则
1. 只允许 SELECT / WITH 语句，任何形式的写操作都将被系统拒绝。
2. 禁止 SELECT *，必须显式列出需要的列。
3. 引用未在上述表结构出现的表或列，即视为任务失败。
4. 若问题未明确时间范围，使用最近 30 天并在 reasoning 中说明该假设。
5. 不确定时，宁可返回 needs_clarification=true 并提问，也不要猜测。
6. 结果集可能很大时必须加 LIMIT。
7. 当前日期为 {today}，“上个月”等相对时间需据此计算具体区间。

# 输出格式
仅输出一个 JSON 对象，不要任何额外文字、不要 markdown 包裹：
{
  "sql": "...",
  "reasoning": "我为什么这样写，做了哪些假设",
  "tables_used": ["..."],
  "confidence": "high|medium|low",
  "needs_clarification": false,
  "clarification_question": null
}

# 用户问题
{question}

# 对话历史（多轮时提供，用于指代消解）
{history}
```

**关键设计逐条说明**：

| 设计点 | 目的 |
|---|---|
| 强约束「只能使用列出的表列」 | 防幻觉最直接的一道闸，配合后置校验 |
| 要求输出 `reasoning` | 强制模型先想后写，并让错误可人工诊断 |
| 允许返回 `needs_clarification` | 把「不确定」变成显式信号，而不是隐式错误 |
| 注入当前日期 | 不注入则「上个月」根本无法正确计算 |
| 只输出 JSON、无包裹 | 保证程序可解析，避免 ```json 包裹导致解析失败 |
| 口径公式优先 | 避免模型自己「发明」GMV 算法 |

### 10.2 错误重试时的附加上下文

执行报错后，**不丢弃原现场**，而是把错误原文递回：

```text
你上一次生成的 SQL 执行失败。

上次 SQL：{previous_sql}
数据库报错：{db_error}

请分析报错原因（常见：列名不存在、表名错误、类型不匹配、函数不支持、
GROUP BY 缺少非聚合列），然后修正。不要重复同样的写法。
```

### 10.3 AnalysisAgent 提示词要点

总结结果时强制约束于事实，防止二次幻觉：

```text
你只能基于下面提供的查询结果作答，不得引入任何未出现的数字或外部知识。
请用简洁中文给出：直接结论 + 关键数据点（带具体数值）+ 显著特征。
若结果为空或只有一行，直白说明，不要编造趋势。

查询结果：{result_preview}
```

### 10.4 图表推荐：规则优于模型

图表类型选择**优先用确定性规则**（见功能设计 3.4 D3），LLM 只负规则无法定断时的兜底判断。原因：图表选型是纯结构问题，规则更快更省 Token 且不会错。

---

## 11. 语义层数据模型

### 11.1 关系型部分（指标字典）

```sql
-- 指标定义表：口径的单一真实来源（Single Source of Truth）
CREATE TABLE metric_definition (
    id            INTEGER PRIMARY KEY,
    code          TEXT UNIQUE NOT NULL,     -- 'gmv'
    display_name  TEXT NOT NULL,            -- '成交总额'
    aliases       TEXT,                     -- '销售额,交易额,流水'  同义词，供检索命中
    formula_sql   TEXT NOT NULL,            -- 'SUM(orders.amount) WHERE status != ...'
    grain         TEXT,                     -- '订单'
    dimensions    TEXT,                     -- 'region,category,month'
    owner         TEXT,                     -- 口径负责人/团队
    description   TEXT,
    updated_at    TIMESTAMP
);

-- 数据源注册表
CREATE TABLE datasource (
    id           TEXT PRIMARY KEY,          -- 'chinook'
    type         TEXT,                      -- sqlite / mysql / postgres
    config_json  TEXT,                      -- 连接参数（密码走环境变量）
    description  TEXT,
    status       TEXT                       -- active / disabled
);

-- 表/列的业务备注（补充数据库本身缺失的注释）
CREATE TABLE column_annotation (
    datasource_id TEXT,
    table_name    TEXT,
    column_name   TEXT,
    business_name TEXT,                      -- '下单金额'
    role          TEXT,                      -- 'dimension' | 'measure' | 'time' | 'key'
    enum_values   TEXT,                      -- 'paid,cancelled,refund'
    sample_values TEXT,
    PRIMARY KEY (datasource_id, table_name, column_name)
);

-- 历史问答（Few-shot 语料）
CREATE TABLE verified_qa (
    id            INTEGER PRIMARY KEY,
    datasource_id TEXT,
    question      TEXT,
    sql_text      TEXT,
    verified      BOOLEAN DEFAULT 0,         -- 只检索已验证的
    hit_count     INTEGER DEFAULT 0,         -- 被命中次数，用于排序权重
    created_at    TIMESTAMP
);
```

### 11.2 向量索引命名空间划分

| Collection | 向量化内容 | 使用者 |
|---|---|---|
| `schema_{ds_id}` | 表描述 + 列描述（含业务名、示例值） | SchemaAgent |
| `glossary_{ds_id}` | 指标 `display_name + aliases + description` | SchemaAgent |
| `fewshot_{ds_id}` | 已验证问题的原文 | SQLAgent |

**设计要点**：向量只负责「找到」，不负责「正确」。真正的口径公式存在关系表（`metric_definition.formula_sql`），命中后原文注入 Prompt，**绝不让 Embedding 检索结果本身充当定义**。

### 11.3 轻量替代方案（起步推荐）

初期不必建表。采用 **YAML 语义层文件**（参照 Snowflake Cortex Analyst 做法），人工可维护、可进版本控制：

```yaml
# semantic_model.yaml
datasource: chinook
metrics:
  - code: gmv
    name: 成交总额
    aliases: [销售额, 交易额, 流水]
    formula: SUM(invoice.Total)
    filter: "invoice.Total > 0"
dimensions:
  - name: 地区
    column: customer.Country
  - name: 月份
    column: invoice.InvoiceDate
    granularity: month
relationships:
  - invoice.CustomerId -> customer.CustomerId
```

优势：无需数据库与向量检索即可实现口径统一，**能解决 70% 的 L2 问题**；后续再升级为向量检索。

---

## 12. 关键技术难点与解法

> 这一节是项目真正的技术含量所在。下列六个难点决定了能不能从 L1 走到 L3。

### 12.1 大 Schema 上下文超长

**问题**：几百张表全量塞 Prompt → Token 爆炸、准确率下降、成本高。

**解法（分层收缩）**：

1. **表级召回**：先用向量检索定位 Top-5 相关表。
2. **列级精筛**：只注入被命中表的列，而非全字段。
3. **外键自动扩展**：召回表的直接关联表自动补上（否则 JOIN 必然缺表）。
4. **丢弃噪声列**：排除大字段（TEXT/BLOB）、内部技术字段（`_sync_at`）、无业务含义的代理键 ID。
5. **压缩表示**：用紧凑格式写 Schema，不用 JSON 嵌套（可节约 40%+ Token）：

```text
# invoice(id INT PK, customer_id INT FK->customer.id, total NUMERIC 金额,
#         invoice_date DATE 下单时间, billing_country TEXT 国家)
```

### 12.2 SQL 方言差异

**问题**：同一句「本月」在 MySQL/PG/SQLite 写法完全不同，模型易串方言。

**解法**：

- Prompt 中**显式声明 dialect**（模板已含 `{dialect}` 占位）并附 2–3 条方言专属示例。
- 方言敏感函数后置校验：如 `DATE_FORMAT`（MySQL）vs `TO_CHAR`（PG）vs `strftime`（SQLite）。
- 进阶方案：用 SQL 解析器（如 `sqlglot`）做方言转译与 AST 级校验。

### 12.3 时间处理（最容易被低估的坑）

**问题**：「上个月」「本季度」「同比」需要模型自己推算日期，一旦基线日期错就全错。

**解法**：

- **不由 LLM 算日期**：后端预处理将相对时间**确定性地解析为具体区间**，作变量注入 Prompt。
- 同时注入「当前日期 + 星期 + 时区」。
- 将常用时间粒度（日/周/月/季）作为维度写进语义层，避免模型自创截断函数。

### 12.4 JOIN 路径不确定（扇出重复计数）

**问题**：多表关系下，模型可能选错 JOIN 路径，导致 `SUM` 被重复行放大（经典事故：GMV 算成两倍）。

**解法**：

- 语义层显式声明 relationships 与基数（1:N / N:M）。
- 对已知陷阱写入规则：如「算订单金额只能用 `orders` 表，不要 JOIN `order_items`」。
- 提供校验型 Few-shot：包含「错误 JOIN vs 正确 JOIN」对比示例。

### 12.5 只读护栏的可靠性

**问题**：正则黑名单易被绕过（注释、大小写变形、多语句拼接、嵌套子查询）。

**解法（双保险，不依赖单点）**：

| 层次 | 手段 | 强度 |
|---|---|---|
| 应用层 | 关键字黑名单 + 起始词校验 + 禁多语句（`;` 分割后只允许 1 条） | 中，防君子 |
| **驱动层** | **SQL 解析为 AST，确认根节点是 Select**（`sqlglot.parse_one`） | 高 |
| **数据库层** | **只读账号 / SQLite `mode=ro`** | **最高，硬保障** |

> 结论：应用层校验可以做得再严，但**真正的安全必须落在数据库账号权限上**。

### 12.6 LLM 输出不可解析

**问题**：要求输出 JSON 但模型给了 ```json 包裹 / 额外解释 / 字段名不一致。

**解法**：

- 优先使用模型的 **Tool Calling / JSON Schema 输出能力**（结构性约束优于口头约定）。
- 降级方案：文本提取首个 `{...}` 块 + `json.loads` 失败时再发一次请求，让模型「只修格式不改内容」。
- 注意部分模型（尤其国产轻量型）**不支持 `json_schema` response_format**，适配层需能回退到提示词约束。

---

## 13. 可观测性与调试

每次问答记录为一条结构化 trace，没有可观测性就无法提升准确率：

```json
{
  "trace_id": "t-7f3a",
  "session_id": "abc-123",
  "question": "各地区 GMV",
  "intent": "取数",
  "rag": {
    "tables_recalled": ["orders", "customers"],
    "metrics_hit": ["gmv"],
    "fewshot_ids": [12, 47],
    "recall_latency_ms": 38
  },
  "sql": {
    "generated": "SELECT ...",
    "retry_count": 1,
    "last_error": "no such column: amout",
    "validated": true
  },
  "execution": {"datasource": "chinook", "rows": 4, "elapsed_ms": 42},
  "llm": {"provider": "deepseek", "prompt_tokens": 2810, "completion_tokens": 190},
  "feedback": null
}
```

**关键指标看板**：SQL 一次成功率、重试后成功率、幻觉拦截命中数、平均 Token 成本、用户点踩率。

**离线回放**：保留失败 trace，形成评测集增补来源 —— 这是准确率持续上升的唯一路径。

---

## 14. 部署形态

### 14.1 开发环境（单进程）

```bash
pip install -r requirements.txt
export DEEPSEEK_API_KEY=sk-xxx
uvicorn app.main:app --reload --port 8000
```

前端本地起 `npm run dev` 或直接打开 `frontend/index.html`。

### 14.2 容器化

```yaml
# docker-compose.yaml
services:
  backend:
    build: ./backend
    env_file: .env
    ports: ["8000:8000"]
    volumes:
      - ./data:/app/data          # SQLite 文件与向量库持久化
  frontend:
    build: ./frontend
    ports: ["80:80"]
    depends_on: [backend]
```

### 14.3 演进路径

| 阶段 | 形态 | 新增依赖 |
|---|---|---|
| 起步 | 单进程 + SQLite + 本地向量文件 + YAML 语义层 | 零外部服务 |
| 进阶 | + Redis（会话/缓存） + 真实业务库 | Redis |
| 生产 | + 独立向量库（Milvus） + 任务队列 + 多副本 + 鉴权 | Milvus / MQ / Auth |

---

## 15. M1 落地任务拆解

> 文档到代码之间需要一份可执行的任务清单。M1 目标：单一数据源上跑通 L1 链路。

| 序号 | 任务 | 产出验收 |
|---|---|---|
| 1 | 初始化项目目录与依赖 | `uvicorn` 能启动 `/health` |
| 2 | 定义 `DataSource` 抽象接口 + SQLite 实现 | 能列出表结构与执行 SELECT |
| 3 | 实现 `mode=ro` 只读连接与超时 | 写操作被数据库层拒绝 |
| 4 | 模型适配层（litellm + config.yaml） | 一行配置切换 DeepSeek/OpenAI/Ollama |
| 5 | SQL 安全校验器 + 表列合法性比对 | 单测覆盖：写操作/幻觉表名/多语句 |
| 6 | 最简语义层：`semantic_model.yaml` + 直接注入 Prompt | 口径公式能出现在 Prompt 中 |
| 7 | SQLAgent（生成→校验→执行→重试循环） | 简单问题能一次或重试后成功 |
| 8 | AnalysisAgent（结果总结 + 图表规则引擎） | 返回 JSON 含 summary/chart |
| 9 | `/api/v1/chat` SSE 接口 | curl 能看到分步事件 |
| 10 | 最小前端（消息框 + 表格 + 图表） | 浏览器能问答并看到图 |
| 11 | 评测集 30 条 + 基线准确率报告 | 得到第一个准确率数字 |

**M1 不做什么（重要）**：不做向量检索、不做多数据源、不做登录权限、不做深度分析循环。先把 L1 的准确率数字测出来，再决定 M2 的投入方向。
