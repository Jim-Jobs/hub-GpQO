# ChatBI 最小原型（可运行）

按《技术设计文档》第 13 节目录结构落地的**可运行最小原型**：跑通
`自然语言 → 受控查询 DSL → 确定性编译为 SQL → 沙箱执行 → 图表 / 结论` 全链路，
并实现**编译期权限注入**、**受控词表校验**、**澄清反问**、**语义层迁库 + 审批/版本管理**、**多轮会话状态服务**、**EX 评测**。

> 核心设计主线：**受控生成 + 两阶段分离**。大模型只输出受控 DSL，编译为 SQL 的过程完全确定性、不依赖模型。

---

## 1. 环境要求

- Python 3.10+（已在 Python 3.12.7 验证）
- `pyyaml`（语义层 YAML 解析）

```bash
pip install pyyaml
```

## 2. 数据源

使用 SQLite 示例库 **chinook**（音乐商店：客户 / 订单 / 订单明细 / 曲目 / 曲风）。

- 默认优先读取工程内 `data/chinook.db`；
- 若不存在，回退到外部示例库路径；
- 可用环境变量 `CHATBI_DB` 指定任意路径：

```bash
set CHATBI_DB=D:\path\to\chinook.db
```

数据锚点：以库内最新业务日期（`MAX(invoices.InvoiceDate)` = `2013-12-22`）作为"今天"，
使 `近3个月`、`上个月` 等相对时间可用。

## 3. 快速开始

```bash
python demo.py                 # 批量演示：全链路 / 权限 / 受控校验 / 多轮会话
python demo.py -i              # 交互式问数
python demo.py --user bob      # 指定用户，演示权限差异
python eval.py                 # 运行 golden 评测集，输出执行准确率 EX
python eval.py eval_multiturn.jsonl   # 多轮会话评测集（含 setup 前置轮次）

python semantic_admin.py init  # 语义层迁库：建元数据库并导入 YAML（首次执行）
python semantic_admin.py demo  # 审批 / 版本管理端到端演示（临时库，见第 7 节）
```

当前评测结果：单轮 **EX = 17/17 = 100.0%**；多轮 **EX = 11/11 = 100.0%**。

## 4. 目录结构

```
chatbi-prototype/
├── semantic/                  # 语义层定义（YAML：编辑期/迁移来源，项目的"单一可信来源"）
│   ├── models.yaml            # 模型：物理表 + 显式 join 路径
│   ├── metrics.yaml           # 指标：原子 / 派生 / 复合
│   ├── dimensions.yaml        # 维度：层级 + 取值映射 + 同义词
│   └── permissions.yaml       # 权限：行级规则 / 列级拒绝 / 列级脱敏
├── chatbi/
│   ├── config.py              # 数据源 / 语义层路径 / 元数据库路径
│   ├── semantic.py            # 注册中心：加载校验、join 图、受控词表索引（YAML / DB 双路径）
│   ├── metastore.py           # ★ 元数据仓储：版本化对象表 + 审计事件表（SQLite/PG 双方言 DDL）
│   ├── approval.py            # ★ 审批工作流：草稿→提交→发布/驳回、回滚、差异、发布前守卫
│   ├── validator.py           # DSL 校验（结构化错误，供修复或澄清）
│   ├── planner.py             # join 图规划（最短路径 + BFS 排序）
│   ├── expression.py          # 指标表达式展开（复合指标递归）
│   ├── permission.py          # 权限上下文
│   ├── compiler.py            # ★ DSL → SQL 编译引擎（含权限注入）
│   ├── executor.py            # SQL 沙箱执行（只读 / 白名单 / 限行）
│   ├── timeres.py             # 相对/绝对时间 → 日期区间
│   ├── chart.py               # 图表推荐
│   ├── summary.py             # 结果结论生成
│   ├── pipeline.py            # 端到端编排 + 同环比 + 语义层来源选择 + 多轮会话入口
│   ├── session.py             # ★ 多轮会话状态：Session / Turn + TTL·LRU 会话存储
│   ├── context.py             # ★ 上下文改写：省略补全 / 指代消解 / 下钻 / 澄清接续
│   └── nlu/
│       ├── parser.py          # ★ 规则式 NL→DSL（受控词表匹配，离线可用）
│       └── llm.py             # 可选 LLM NL→DSL（OpenAI 兼容接口）
├── data/
│   └── chatbi_meta.db         # 语义层元数据库（init 生成，可用 CHATBI_META_DB 覆盖）
├── demo.py                    # 演示脚本
├── eval.py                    # 评测脚本（单轮 / 多轮）
├── eval_cases.jsonl           # 单轮 golden 评测集
├── eval_multiturn.jsonl       # 多轮 golden 评测集（含 setup 前置轮次）
├── semantic_admin.py          # ★ 语义层管理 CLI：迁库 / 审批 / 版本
└── README.md
```

## 5. 设计映射

| 技术设计文档 | 代码实现 |
| --- | --- |
| 语义层 DSL 数据模型（第 3 节） | `semantic.py` + `semantic/*.yaml` |
| 元数据数据模型（第 8 节） | `metastore.py`（版本化对象表 + 审计事件表，双方言 DDL） |
| 审批与版本管理（第 2.2.5 节） | `approval.py` + `semantic_admin.py` |
| 受控查询 DSL（第 3.3 节） | `validator.py`（白名单校验） |
| DSL→SQL 编译流程（第 4.1 节） | `compiler.py`（校验→规划→展开→权限→生成） |
| 编译期权限注入（第 6.2 节） | `compiler.py` + `permission.py` |
| NL→DSL 链路（第 5 节） | `nlu/parser.py`（规则基线）+ `nlu/llm.py`（可选 LLM） |
| 多轮上下文（第 5 节） | `session.py`（会话状态）+ `context.py`（上下文改写） |
| 沙箱执行与安全（第 6.1 节） | `executor.py` |
| 评测体系（第 11 节） | `eval.py` + `eval_cases.jsonl` / `eval_multiturn.jsonl` |

## 6. 已实现能力

- **语义层**：模型 / 指标（原子·派生·复合）/ 维度（层级·取值映射·同义词）/ join 图，加载时自校验（模型可达性、指标引用、循环依赖）
- **受控 DSL 校验**：指标存在性、维度对指标的可用性、算子白名单、取值受控、时间合法性、limit/order_by
- **确定性编译**：join 图最短路径规划、复合指标递归展开、维度 CASE 自动生成、时间区间裁剪
- **权限强制注入**：行级规则注入 WHERE、列级拒绝（报错）、列级脱敏（`******`）——均在编译期完成，不经过模型
- **NL→DSL**：规则解析器（受控词表最长匹配 + 位置占位消歧、时间/排名/同环比识别），可选 LLM 兜底
- **澄清反问**：无指标 / 指标歧义 → 返回澄清卡片而非猜测
- **结果呈现**：图表推荐（number/line/bar/pie/table）+ 中文结论摘要
- **同环比**：区间前移一期计算 delta / ratio
- **语义层迁库**：YAML → 元数据库幂等导入，运行时只读"已发布"版本（发布即生效，无需重启）
- **审批与版本**：草稿/待审/发布/驳回/下线状态机、审批审计链、版本历史、差异对比、回滚（新建草稿而非删历史）、发布前一致性守卫
- **多轮会话**：会话/轮次状态管理（TTL 续期 + LRU 淘汰）、上下文改写（省略补全 / 指代消解 / 下钻 / 澄清接续），并保证**无追问标记时绝不继承**（防止上下文污染）
- **评测**：EX 度量（结果多重集比对，忽略行/列顺序，数值容差 0.01）

## 7. 语义层迁库与审批/版本管理（可运行）

语义层的"单一可信来源"已从 YAML 文件迁移到**元数据库**，并实现**审批工作流 + 版本管理**。

### 7.1 为什么迁库

| 维度 | YAML 文件 | 元数据库 |
| --- | --- | --- |
| 变更留痕 | git diff | 版本表 + 审计事件表 |
| 审批 | 无 | 草稿 → 待审批 → 发布 / 驳回 |
| 回滚 | 手工拼 commit | `rollback` 新建草稿，历史不物理删除 |
| 权限管控 | 弱 | 按 owner / 操作人留痕 |
| 运行时生效 | 重启读文件 | 只读"已发布"版本，发布即生效 |

### 7.2 数据模型（对应《技术设计文档》第 8 节）

- `semantic_object`：`obj_type(model|metric|dimension)` + `name` + `version` + `status` + `payload(JSON)`，
  `UNIQUE(obj_type, name, version)` 构成不可变版本历史。
- `semantic_event`：审批流每一步动作（`create/edit/submit/approve/reject/rollback/import`）的审计链。
- 状态机：`draft → pending → published / rejected`；发布新版本时旧 `published` 自动转 `deprecated`。

### 7.3 建库与迁移

```bash
python semantic_admin.py init                     # 建表 + 把 semantic/*.yaml 全量幂等导入（17 个对象，v1 已发布）
python semantic_admin.py list                     # 列出全部对象（各显示最新版本与状态）
python semantic_admin.py ddl --dialect postgres   # 输出 PostgreSQL 建表 DDL（生产库执行）
```

导入是幂等操作：已存在的对象自动跳过，可安全重复执行。

### 7.4 一次完整审批（示例）

```bash
# 1) 编辑：基于线上版本生成草稿；同一草稿连续修改原地覆盖，不刷版本号
python semantic_admin.py edit metric gmv --set "display_name=销售额（GMV）" --actor alice --comment "统一对外口径"

# 2) 提交审批：草稿 → 待审批（待审批版本不可直接编辑，必须先驳回，避免绕过审计链）
python semantic_admin.py submit metric gmv --actor alice --comment "请数据治理组复核"

# 3) 审批通过并发布：发布前做一次"候选语义层"一致性校验；旧的已发布版本自动下线
python semantic_admin.py approve metric gmv --actor dba --comment "口径确认无误"

# 4) 版本历史与审计链
python semantic_admin.py history metric gmv

# 5) 回滚：以目标版本 payload 新建草稿，仍需审批才生效（历史不删除）
python semantic_admin.py rollback metric gmv --to 1 --actor alice
python semantic_admin.py submit metric gmv --actor alice
python semantic_admin.py approve metric gmv --actor dba

# 版本差异对比
python semantic_admin.py diff metric gmv --from 2 --to 3

# 端到端演示（在临时库中进行，不污染正式库）
python semantic_admin.py demo
```

发布后**运行时立即生效、无需重启**：`ChatBI` 每次启动读取元数据库中"已发布"的最高版本。
（可在 `data/` 目录见到 `chatbi_meta.db`。）

### 7.5 运行时加载优先级

`ChatBI(semantic_source="auto"|"db"|"yaml")`：

- `auto`（默认）：元数据库存在且含已发布对象 → 走 DB；否则回退 YAML。`demo.py` 头部会打印实际来源。
- `db`：强制走 DB，无已发布对象则报错。
- `yaml`：强制走 YAML，便于对照调试。

元数据库路径可用环境变量 `CHATBI_META_DB` 覆盖，默认 `data/chatbi_meta.db`。

## 8. 多轮会话状态服务（可运行）

对话式问数的难点不在单句解析，而在**「这句话缺了什么、该从上下文补什么、什么时候绝不能补」**。
本节实现：会话/轮次状态管理 + 上下文改写，并严格防止上下文污染。

### 8.1 分层：谁记、谁用

| 模块 | 职责 |
| --- | --- |
| `session.py` | **记住什么**：`Session`（会话）/ `Turn`（轮次，含 DSL、结果、状态、改写模式） |
| `context.py` | **怎么用**：把省略句/指代句结合会话状态补全为完整 DSL |

`SessionStore` 以 `data/chatbi_meta.db` 为后端（`chat_session` / `chat_turn` / `chat_clarify`），
`expires_at` 滑动续期、容量按 `updated_at` 淘汰，默认 1800s / 500 会话；
对外只暴露 `create / get / get_or_create / drop / clear / stats`。Web 化时可用 Redis 实现同一接口替换。

### 8.2 五种上下文改写模式

| 模式 | 触发 | 行为 |
| --- | --- | --- |
| `new` | 无追问标记 | 视为全新问题，**绝不继承**（防污染） |
| `borrow` | 本轮解析不出指标，但识别到维度/时间等槽位，且句子含"呢/那/按…看" | 指标沿用上一轮，其余槽位取本轮显式值、缺失部分继承 |
| `followup` | 本轮能解析出指标，且带追问标记（呢 / 那 / 换成） | 本轮显式槽位覆盖上一轮，未提及的槽位继承 |
| `clarify_pick` | 上一轮在澄清，本轮输入是对候选的直接选择（如"销售额"） | 用澄清时已识别的维度/时间 + 本轮选定指标，组装完整 DSL |
| `anaphora` | 出现"这些 / 上面"等指代词 | 用**上一轮结果集**中该维度的取值，生成 `IN (...)` 筛选 |

`rewrite()` 每次返回可读的 `note`（"我是这样理解你的"）与 `mode`，随结果一并返回给前端展示。

### 8.3 用法

```python
from chatbi.pipeline import ChatBI

chatbi = ChatBI()                     # 语义层来源 semantic_source="auto"
r1 = chatbi.chat("各大区销售额")        # {'session_id': '…', 'turn': 1, 'context': {'mode': 'new'…}}
sid = r1["session_id"]

chatbi.chat("那上个月呢", session_id=sid)     # borrow：沿用 销售额+大区，换时间
chatbi.chat("换成订单数呢", session_id=sid)   # followup：换指标，继承维度与时间
chatbi.chat("这些城市的销售额", session_id=sid) # anaphora：用上一轮城市的取值做筛选

chatbi.reset(sid)                     # 结束会话（清空上下文）
```

返回结构中新增三个字段：`session_id`（后续请求带上即可续聊）、`turn`（轮次号）、
`context`（`{mode, note}`，即"本轮如何理解上下文"）。

### 8.4 防污染设计（关键）

- **无追问标记即不继承**：`_borrowable()` 要求句子里必须有"呢/那/换成/按…看"这类省略标记，
  否则即使含有时间词（如"今天天气怎么样"）也不从上下文借指标，直接澄清。
- **受控语义层边界**：若本轮**显式要求**的维度不被继承的指标支持（如"销售额" + "按曲风看"），
  **不静默忽略**，而是澄清并提示哪些指标支持该维度（曲风 → 曲目销量）。
- **状态只在成功轮沉淀**：`context_dsl()` 取最近一次 `status == ok` 且有数据行的轮次，
  澄清/报错轮次不参与上下文继承。

### 8.5 多轮评测

`eval_multiturn.jsonl` 用 `setup` 字段声明前置轮次，在同一会话中依次执行后再评测当前问句：

```
python eval.py eval_multiturn.jsonl
# [PASS] #01 (alice) 各大区销售额 → 那上个月呢  [borrow]
# [PASS] #04 (alice) 销售额Top5的国家 → 这些国家的销售额  [followup]
# [PASS] #09 (alice) 各大区销售额 → 今天天气怎么样  [clarify]
# 执行准确率 EX = 11/11 = 100.0%
```

## 9. 一次问数的端到端示例

输入：`近3个月华东...` → 本例用 `北美和南美哪个销售额高`

```
DSL: {'metric':'gmv','dimensions':['region'],
      'filters':[{'dimension':'region','op':'in','value':['北美','南美']}]}

SQL: SELECT CASE WHEN c.Country IN ('USA','Canada') THEN '北美' ... END AS "region",
            (SUM(i.Total)) AS "gmv"
     FROM invoices i JOIN customers c ON i.CustomerId = c.CustomerId
     WHERE (CASE ... END IN ('北美','南美'))
     GROUP BY 1

结论: 共 2 组数据；销售额最高的是 大区=北美（827.02）；最低的是 大区=南美（274.34）。
```

## 10. 如何扩展

> 语义层已迁库，运行时默认读元数据库。**正式变更请走平台审批**：
> `semantic_admin.py edit ... → submit ... → approve ...`（见第 7.4 节）。
> 若只想本地快速试验，可直接改 YAML 并用 `ChatBI(semantic_source="yaml")` 对照验证；
> 新增对象（YAML 里没有的）可再次执行 `semantic_admin.py init` 幂等导入。

**新增指标**（改 `semantic/metrics.yaml`，无需改代码）：

```yaml
- name: avg_qty
  display_name: 平均每单曲目数
  metric_type: composite
  model: fact_invoice
  expression: "${track_qty} / ${order_cnt}"
  synonyms: [平均每单曲目数, 单均曲目]
  time_model: fact_invoice
  time_dimension: month
  time_column: i.InvoiceDate
  available_dimensions: [region, country, month]
```

**新增维度**：在 `dimensions.yaml` 增加定义（给出 `expression` 或 `value_mapping`+`base_column`），
并把维度名加入相关指标的 `available_dimensions`；加载时会自动校验 join 路径可达性。

**新增业务域**：复制一份 YAML 集合并补充 `models.yaml` 的 join 关系，即接入新数据域。

## 11. 已知边界（按 MVP 范围）

| 项 | 现状 | 后续 |
| --- | --- | --- |
| 同环比 | 仅支持无维度分组的整体对比 | 支持分组内同环比 |
| 归因分析 | 未实现 | 下钻 + 异常检测 |
| 列级脱敏 | 直接替换为常量，分组会坍缩 | 改为掩码 / 聚合降维 |
| 语义层编辑 | 已有元数据库 + 审批/版本 CLI，仍为命令行操作 | 平台化可视化编辑器（复用同一元数据模型） |
| 审批粒度 | 单对象逐条审批 | 变更集（Changeset）批量评审、多级审批 |
| 多轮会话 | 已实现会话状态 + 上下文改写（borrow/followup/anaphora/clarify_pick），进程内 TTL 存储 | 分布式会话存储（Redis）、跨会话长期记忆 |
| 召回 | 词表精确匹配 | 向量检索 + 混合召回 |
| 存储 | 元数据库为 SQLite（已可输出 PG DDL） | 生产切 PostgreSQL（见技术文档第 8 节） |
| 缓存 | 未实现 | Redis 结果/编译缓存（见技术文档第 4.3 节） |

## 12. 启用 LLM 解析（可选）

规则解析器是基线；如需用大模型做 NL→DSL（仍受 `validator.py` 强约束）：

```bash
set CHATBI_LLM_BASEURL=https://api.openai.com/v1
set CHATBI_LLM_KEY=sk-xxx
set CHATBI_LLM_MODEL=gpt-4o-mini
```

然后 `ChatBI(use_llm=True)`。LLM 不可用或输出不合法时自动回退到规则解析器。
