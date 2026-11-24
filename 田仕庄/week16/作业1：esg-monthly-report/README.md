# ESG 月报生成 Skill —— README

> **版本**：v0.1 ｜ **更新**：2026-11-23 ｜ **状态**：已落地（引擎 / 测试 / CI），端到端运行需真实 API Key

> 一个可复用的 ESG 月报自动化 Skill：输入「客户公司 + 报告月份」，自主联网检索、筛选、总结，输出约 5000 字、含 3–4 张表的结构化中文月报（HTML 主稿，可选 Word 导出）。首个演示案例：**中国神华**。

---

## 0. 项目状态（请先读）

| 项 | 状态 |
|---|---|
| 代码实现 | ✅ 已实现（`scripts/` 引擎 + `tests/` 契约测试，49 项全绿） |
| 运行环境 | ✅ conda `py312`（Python 3.12，依赖已锁定，见 §5） |
| CI | ✅ 已就位（`.github/workflows/ci.yml`：lint + mypy 类型检查 + 离线单测 + 依赖安全扫描） |
| 首个演示案例 | 本机已跑通（2026-10 中国神华；产物在 `output/`，不入库） |
| 验收 | 未开始 |

> 代码已按本文规格落地；端到端生成报告仍需真实 API Key。测试可离线运行：`python -m pytest tests/`。

---

## 1. 项目简介

ESG 月报是上市公司董办、IR、可持续发展部门的刚性需求，但人工撰写存在**信息源分散、时效性强、口径不统一、逐条溯源成本高**四大痛点。本 Skill 将月报生产拆解为「检索 → 筛选 → 总结 → 成文 → 质检」五段流水线，以配置驱动的方式适配不同客户与行业，并沉淀可复用的 Python 脚本。

**一句话定位**：给定客户与月份，产出一份**每条信息可溯源、影响可判断、建议可执行**的 ESG 月报初稿，人工修改率目标 < 20%。

---

## 2. Skill 与引擎的边界（重要）

本仓库包含两个不同性质的部分，职责边界如下：

| 部分 | 形态 | 职责 |
|---|---|---|
| **Skill** | `SKILL.md`（Agent 提示词/编排指令） | 触发条件判断、流程编排、质检判断、人工复核清单生成、报告措辞与合规把关 |
| **引擎** | `scripts/`（Python 流水线） | 检索、去重、打分、模板渲染、Word 导出等确定性、可单元测试的环节 |

**分工原则**：检索 / 打分 / 渲染等「确定性、可测试」的逻辑下沉到引擎；总结 / 影响判断 / 建议撰写等「依赖判断力、存在幻觉风险」的环节留在 Skill 层，并强制走「来源 + 影响等级 + 一句话」的结构化约束。

**LLM 的位置**：总结环节需要调用 LLM，但 LLM 只由引擎 `summarize.py` 发起（提示词组装、分块、重试、格式校验）；「影响等级怎么判、一句话怎么写」的判断规则由 `SKILL.md` 定义下发。检索与渲染不依赖 LLM，保证可离线复跑与单测。

---

## 3. 核心能力

| 能力 | 说明 | 归属 |
|---|---|---|
| 自主联网检索 | 按配置的 query 模板并发检索中英文多源信息 | 引擎 |
| 信息筛选打分 | 按相关性、权威性、时效性三维打分，去重后取 Top-N | 引擎 |
| 结构化总结 | 每条信息强制四要素（定义见 §10） | Skill（引擎仅分块 + 格式约束） |
| 报告渲染 | Jinja2 模板渲染 HTML 主稿，可选 Word 导出 | 引擎 |
| 幻觉控制 | 无来源不写；不可访问来源降级为「待核实」单列（机制见 §10） | Skill + 引擎 |
| 质量自检 | 生成后输出人工复核清单（来源覆盖率、缺口、待核实项） | Skill |

---

## 4. 目标用户与场景

- **上市公司董办 / IR / 可持续发展部**：月度 ESG 舆情与政策跟踪。
- **ESG 咨询 / 评级服务机构**：为客户批量产出行业月报。
- **投资机构 ESG 研究**：跟踪持仓标的的政策与同业动态。

典型触发语：「帮我生成中国神华 2026 年 10 月的 ESG 月报」。

---

## 5. 快速开始

### 5.1 前置条件

- Python ≥ 3.12（3.10 已于 2026-10 停止安全更新，3.12 仍在维护窗口内）
- 一个可用的 Web 搜索 API（SerpAPI / Bing / Tavily / 博查，任选其一，Key 写入 `config/.env`，见 §7.3）
- 一个可用的 LLM API（总结环节使用；按 OpenAI 兼容协议接入，`LLM_BASE_URL` 可替换提供方，Key 写入 `config/.env`，见 §7.3）
- 可选：Word 导出能力（见 §12）

### 5.2 安装

依赖已在 `py312` 校准锁定（LLM 走 OpenAI 兼容 HTTP 接口，不引入 SDK）。本机开发统一使用 conda 环境 `py312`，不新建 `.venv`：

```bash
conda activate py312
python -m pip install -r requirements.txt -r requirements-dev.txt

# 填入搜索 / LLM API Key（.env 不入库，见 §11）
cp config/.env.example config/.env
```

运行时依赖见 `requirements.txt`（5 项，`==` 锁定）；开发与 CI 工具（`ruff` / `mypy` / `pytest` / `pip-audit`）见 `requirements-dev.txt`。

依赖一律用 `==` 精确锁定以保证可复现；升级依赖须走 PR，并通过 §11 的依赖安全扫描。

### 5.3 配置客户

编辑 `config/client_shenhua.yaml`，至少确认：行业口径、对标企业清单、关键词、合规红线。

### 5.4 运行

```bash
# 生成 2026 年 10 月月报（时间口径见 §8.1）
python scripts/run.py --client shenhua --month 2026-10 --format html

# 同时导出 Word
python scripts/run.py --client shenhua --month 2026-10 --format html,docx
```

产物输出至 `output/shenhua/2026-10/report.html`（及 `.docx`）。

---

## 6. 目录结构

以下为当前仓库结构（本地目录名可不同）：

```
esg-monthly-report/                # 仓库根目录
├── README.md                     # 本文（设计规格 + 落地约定）
├── AGENTS.md                     # AI 编码代理协作约定
├── SKILL.md                      # Skill 触发条件、流程、质量规则
├── requirements.txt              # 精确锁定的依赖清单（== 固定版本）
├── requirements-dev.txt          # 开发 / CI 工具（ruff / mypy / pytest / pip-audit，== 锁定）
├── ruff.toml                     # lint 规则（E/F/W，忽略 E501）
├── mypy.ini                      # 类型检查配置（scripts + tests）
├── .gitignore                    # 排除 .env、output/、缓存
├── .gitattributes                # 行尾规范化（跨平台 LF / CRLF）
├── .github/
│   └── workflows/
│       └── ci.yml                # GitHub Actions：lint + 单测 + 依赖安全扫描
├── config/
│   ├── .env.example              # 搜索 / LLM API Key 占位（不含真实 Key）
│   ├── client_shenhua.yaml       # 客户画像：行业、对标、关键词、红线
│   └── sources.yaml              # 权威源白名单 + query 模板
├── scripts/
│   ├── __init__.py               # 包标记（mypy / 包导入）
│   ├── run.py                    # 主入口：编排全流程
│   ├── contracts.py              # 中间产物 pydantic 模型 + JSON Schema 导出
│   ├── search.py                 # 多 query 并发检索 + 限流重试 + 去重
│   ├── rank_filter.py            # 三维打分、阈值过滤、每类 Top-N
│   ├── summarize.py              # 分块总结 + 影响分析
│   └── render_report.py          # Jinja2 渲染 HTML / 直出 Word
├── tests/                        # 单元测试 + 快照测试
│   ├── conftest.py               # 共享 fixture（样本 profile / 条目）
│   ├── test_contracts.py         # 契约 Schema 与 pydantic 模型一致性
│   ├── test_rank_filter.py       # 三维打分 / 阈值 / 每类 Top-N
│   ├── test_render_sections.py   # 章节统计 / 摘要截断 / 缺口渲染
│   ├── test_render_snapshot.py   # Jinja2 渲染快照
│   ├── test_report_stats.py      # 字数 / 表格统计
│   ├── test_run_paths.py         # 入口参数路径校验
│   ├── test_search.py            # 去重 / 来源字段 / 错误诊断
│   ├── test_summarize_retry.py   # LLM 单块重试降级
│   ├── test_time_window.py       # 时间窗口归属
│   └── snapshots/                # 快照基线（monthly_report.html）
├── templates/
│   └── monthly_report.html.j2    # HTML 主稿模板
├── schemas/                      # 中间产物 JSON Schema（contracts.py 导出，入库）
│   ├── raw.schema.json
│   ├── ranked.schema.json
│   └── summarized.schema.json
├── references/
│   ├── esg_standards.md          # 评级/标准速查（ISSB/GRI/MSCI/CDP 等）
│   └── industry_keywords.md      # 行业关键词与 query 库
└── output/                       # 报告产物与中间 JSON（不入库）
    └── shenhua/2026-10/
        ├── report.html           # HTML 主稿
        ├── report.docx           # Word 导出（可选）
        ├── review_checklist.md   # 人工复核清单
        ├── raw.json              # 检索原始结果（query / api / fetched_at）
        ├── ranked.json           # 打分筛选结果（三维得分 / Top-N）
        └── summarized.json       # 总结结果（来源 / 影响等级 / 一句话）
```

---

## 7. 配置文件说明

### 7.1 `config/client_shenhua.yaml`（客户画像）

关键字段：

| 字段 | 说明 | 神华默认值 |
|---|---|---|
| `industry` | 行业口径，决定行业新闻抓取范围 | 煤炭开采与煤电一体化 |
| `peers.domestic` | A 股对标 | 陕西煤业、中煤能源、兖矿能源 |
| `peers.international` | 国际对标 | Peabody、Glencore |
| `keywords` | 检索关键词组 | 双碳、能源保供、绿色矿山、CCUS、安全生产等 |
| `red_lines` | 合规红线 | 不预测政策、不评论短期煤价 |
| `report_length` | 目标字数 | 5000 |
| `table_count` | 目标表格数 | 3–4 |

> 以上为配置默认值；验收区间以 §14 为准。

### 7.2 `config/sources.yaml`（源与 query）

- **权威源白名单**：生态环境部、国家发改委、国家能源局、证监会/交易所、财政部、ISSB、GRI、MSCI、S&P Global、CDP、中国神华官网/公告/公众号、中国煤炭工业协会。
- **补充源**：财新、证券时报、路透、彭博。
- **query 模板**：按「政策 / 评级标准 / 行业 / 客户 / 对标」五类分组，支持 `{month}`、`{client}`、`{peer}` 占位符。

### 7.3 `config/.env`（密钥，不入库）

仅提交 `.env.example` 占位；真实密钥写入本地 `.env`（安全边界见 §11）：

| 变量 | 用途 | 必需 |
|---|---|---|
| `SEARCH_API_KEY` | 搜索 API 密钥（引擎 `search.py`） | 是 |
| `SEARCH_API_PROVIDER` | 搜索 API 提供方（serpapi / bing / tavily / bocha） | 是 |
| `LLM_API_KEY` | 总结环节 LLM 密钥（引擎 `summarize.py`） | 是 |
| `LLM_MODEL` | 总结所用模型名 | 是 |
| `LLM_BASE_URL` | OpenAI 兼容接口地址（境内模型可替换） | 视提供方而定 |

---

## 8. 工作流（五段流水线）

```
① 检索          ② 筛选打分        ③ 总结           ④ 成文            ⑤ 质检
search.py  →  rank_filter.py  →  summarize.py  →  render_report.py  →  复核清单
多源并发         三维打分去重       影响分析          HTML/Word          覆盖率/缺口
```

1. **检索**：按 `sources.yaml` 的 query 模板并发检索；每个 query 需带**限流、超时、退避重试**，避免触发搜索 API 配额上限。来源字段优先取结果自带站点名，缺失时回退 URL 域名。
2. **筛选**：相关性 / 权威性 / 时效性三维打分，去重（URL + 标题相似度），每类取 Top-N。相关性以客户关键词命中为主，叠加该条检索词命中加成（覆盖中文关键词下的标题与摘要匹配）；默认阈值 0.4。
3. **总结**：由 `summarize.py` 调用 LLM 完成（判断规则见 `SKILL.md` §5），每条信息按 §10 四要素输出，一句话 40–80 字。
4. **成文**：Jinja2 渲染 HTML 主稿；Word 采用 python-docx 直出以保证表格样式（见 §12）。
5. **质检**：生成人工复核清单——来源可追溯率、信息缺口、待核实项、建议可执行性抽查。

### 8.1 时间窗口语义（避免歧义）

- **主窗口**：目标自然月（如 2026-10-01 至 2026-10-31）。
- **补充窗口**：主窗口**起始日前 30 天**（用于捕获月底发布、延期入库的上月信息）。
- **去重口径**：以**事件实际发生日期**（而非抓取日期）归属到对应窗口，同一事件取最早来源。
- **时区**：「自然月」「次月 3 日前」及窗口边界均以**北京时间（UTC+8）**为准。

> 运行时机为「次月 3 日前」，补充窗口是向前回溯主窗口起始日，而非运行日，避免与主窗口大幅重叠。

---

## 9. 报告结构

篇幅与表格数量目标见 §14；结构如下：

| 章节 | 内容 | 表格 |
|---|---|---|
| 阅读说明 | 窗口口径、影响等级定义、检索缺口说明 | — |
| 执行摘要 | 本期 5 条最重要动态 + 一句话影响 | — |
| 一、ESG 政策 / 评级 / 标准动态 | 国内外政策、评级变动、标准更新；章节收录统计与重点影响 | **表1 政策与标准动态清单**（来源/日期/影响等级） |
| 二、行业动态与最佳实践 | 煤炭与煤电一体化行业新闻、同业最佳实践 | **表2 行业最佳实践对比** |
| 三、客户公司动态与 ESG 影响 | 神华本期动态、ESG 影响、机会/风险建议（战略+执行各 1 条） | **表3 客户动态影响矩阵** |
| 四、对标企业关键 ESG 行动 | 3 家 A 股 + 2 家国际同行 | **表4 对标行动对比** |
| 五、下月关注 | 高影响条目跟踪、待核实事项与检索缺口 | — |
| 附录 | 来源清单（可点击链接）、评级对标（缺失标 N/A，本期未接入统一 N/A） | — |

每条信息强制格式见 §10；表格附来源摘要（截断 150 字，避免整段转载）。

---

## 10. 质量与合规规则

- **无来源不写**：所有事实性表述必须附可访问链接；不可访问则移入「待核实」。
- **不预测政策、不评论短期煤价**：只做事实归纳与影响分析。
- **四要素**：每条信息 = 来源 + 日期 + 影响等级 + 对客户影响一句话。
- **影响分级**：高/中/低/待核实的判定标准见 `SKILL.md` §5（引擎不硬编码）。
- **建议颗粒度**：战略级 + 执行级各 1 条，标注「机会 / 风险」。
- **评级数据**：仅列公开可查项，缺失标 `N/A`，不推测。
- **人工复核**：报告末附复核清单，供董办/IR 二次确认。

> **关于「幻觉控制」的诚实表述**：这是「约束 + 兜底」机制，**不是零幻觉保证**。约束 = 无来源不写；兜底 = 无法验证的内容降级为「待核实」单列。最终质量依赖人工复核兜底，量化目标见 §14。

---

## 11. 安全与数据合规

- **密钥**：`config/.env` 必须被 `.gitignore` 排除，仅提交 `.env.example` 占位；生产环境建议走环境变量 / 密钥管理服务，不落盘明文。
- **敏感数据**：ESG 月报可能含上市公司未公开 / 影响市场的敏感信息。`output/` 目录整体不入库，且需明确数据留存周期与访问权限。
- **LLM 数据合规**：总结环节会把客户动态与来源摘要发送至 LLM 提供方。发送前须脱敏（去除未公开重大事项细节），境内客户优先境内合规模型；与提供方确认「不留存 / 不用于训练」条款，必要时改为私有化部署。
- **依赖审计**：`pip-audit` 依赖安全扫描纳入 CI，阻断已知漏洞依赖。
- **来源合规**：仅使用有公开转载授权的来源，避免抓取需登录 / 付费墙后的受版权内容。

---

## 12. 脚本说明

| 脚本 | 职责 | 关键参数 |
|---|---|---|
| `run.py` | 编排全流程、读写中间 JSON | `--client --month --format [--topn --threshold]` |
| `contracts.py` | 中间产物 pydantic 模型定义、JSON Schema 导出 | 模块调用：`json_schema` / `export_schemas` |
| `search.py` | 多 query 并发检索、限流重试、URL/标题去重 | 模块调用：`SearchClient` / `fetch_all` / `dedupe` |
| `rank_filter.py` | 三维打分、阈值过滤、每类 Top-N | 模块调用：`score` / `rank` |
| `summarize.py` | 分块调用 LLM + 格式校验（影响判断规则由 `SKILL.md` 下发） | 模块调用：`summarize` |
| `render_report.py` | Jinja2 渲染 HTML、python-docx 直出 Word | 模块调用：`render` / `render_docx` |

中间产物 `output/<client>/<month>/raw.json → ranked.json → summarized.json` 便于复跑与审计，并需在 `raw.json` 中记录**检索时间戳、query、所用 API** 以实现可追溯。LLM 调用只发生在 `summarize.py`，检索与渲染不依赖 LLM，可离线复跑；`summarized.json` 需记录所用模型名与调用时间以便审计。

### 12.1 中间产物字段契约（最小集）

三个阶段产物即模块间接口；最小字段集如下，由 `scripts/contracts.py` 以 pydantic 模型定义并导出 JSON Schema 至 `schemas/*.schema.json`。所有产物均含 `schema_version` 与 `generated_at`；契约变更后运行 `python scripts/contracts.py` 重新导出（测试会校验 schema 与模型一致）：

| 文件 | 生产 → 消费 | 字段（最小集） |
|---|---|---|
| `raw.json` | `search.py` → `rank_filter.py` | `schema_version`、`generated_at`、`api`、`window`（`start / end`）、`gaps[]`（`query / category / reason`）、`results[]`（`title / url / snippet / published_at / source / query / category / api / fetched_at`） |
| `ranked.json` | `rank_filter.py` → `summarize.py` | `schema_version`、`generated_at`、`threshold`、`topn`、`groups{}`（原字段 + `score.relevance / authority / timeliness / total`、`dedup_key`） |
| `summarized.json` | `summarize.py` → `render_report.py` | `schema_version`、`generated_at`、`model`、`items[]`（原字段 + `date / section / impact_level / impact_one_liner / todo`） |

### 12.2 Word 导出说明

采用 **python-docx 直出**，以保证 4 张表的样式与列宽可控；不推荐 `pandoc HTML → docx`（表格样式易丢失、错位）。若未来需要复杂排版，再评估模板驱动的 `docxtpl`。

---

## 13. 测试与 CI

- **单元测试**：覆盖打分规则、去重逻辑（URL + 标题相似度）、时间窗口归属、LLM 单块重试降级、字数 / 表格统计、契约 Schema 与 pydantic 模型一致性、渲染章节统计与来源摘要截断、入口参数路径校验、提示注入隔离与 4xx 快速失败。
- **快照测试**：固定输入 fixture，对 Jinja2 模板渲染结果做快照比对，防止模板回归。
- **CI（GitHub Actions）**：`main` 推送与 PR 触发 `ruff check` + `mypy` 类型检查 + 离线单测 + `pip-audit` 依赖扫描，工作流见 `.github/workflows/ci.yml`；当前仅做质量门禁，每月 3 日自动跑上月的定时任务尚未接入（见 §16）。
- **Secrets**：定时任务所需 `SEARCH_API_KEY`、`SEARCH_API_PROVIDER`、`LLM_API_KEY`、`LLM_MODEL` 存放于 GitHub 仓库级 Secrets，仅在 workflow 中注入，禁止打印或落盘。

---

## 14. 验收标准（含计量口径）

| 指标 | 目标 | 计量方法 |
|---|---|---|
| 来源可追溯率 | ≥ 95% | （含可访问链接的信息条数 ÷ 事实性信息总条数）× 100%，由质检脚本自动统计 |
| 人工修改率 | < 20% | 基于 `difflib.SequenceMatcher` 的字符相似度：修改率 = 1 − similarity(初稿, 定稿)；验收口径为累计 ≥ 10 篇的均值，或连续 3 个月逐月达标 |
| 幻觉率 | ≤ 3% 且逐月不上升 | 每篇抽样 ≥ 20 条事实性表述逐条核对来源，不符条数 ÷ 抽检条数（需人工，无法自动） |
| 表格数量 | 3–4 张 | 自动统计，写入复核清单 |
| 字数 | 4500–5500 | 自动统计，写入复核清单（CJK 字符 + 拉丁词） |
| 信息缺口 | 明确标注，不隐藏 | 人工复核清单核对 |
| 待核实项 | 单列，附原因 | 人工复核清单核对 |
| 单次运行成本 | ≤ ¥X/篇（落地后实测校准） | 搜索 API 调用费 + LLM tokens 费，按账单核对 |

---

## 15. 示例：中国神华首期月报

2026-10 中国神华月报已在本机跑通。运行命令同 §5.4，产物路径与清单见 §6，篇幅与表格目标见 §14；样例产物在 `output/`，不入库。

---

## 16. 扩展与路线图

- [x] 代码落地（`scripts/` 引擎 + `tests/` 契约测试）
- [x] CI 骨架（lint + 单测 + 依赖安全扫描）
- [ ] 多客户批量模式（`--clients shenhua,peer_a`）
- [ ] 接入巨潮/交易所公告结构化 API
- [ ] 评级数据源对接（MSCI / Wind / 华证）
- [ ] GitHub Actions 定时任务（每月 3 日自动跑上月）
- [ ] PPT 摘要版输出
- [ ] 行业配置模板库（煤炭、电力、钢铁、化工……）

---

## 17. 常见问题

**Q：搜索 API 用哪家？** 任选，Key 写入 `config/.env`，`search.py` 做了适配层；各 API 均有 QPS 上限，引擎已内置限流与退避重试。

**Q：没有付费搜索 API 怎么办？** 本流水线的检索环节依赖搜索 API（检索本身需付费），「公开源」指的是**抓取的内容是公开网页**，而非免费调用；无 Key 时流水线无法运行，只能退回人工录入。

**Q：检索配额耗尽 / LLM 超时怎么办？** 检索侧：单 query 的退避重试耗尽后跳过，并记入「信息缺口」，不阻塞整体流程；LLM 侧：单块总结超时重试后降级为「纯检索摘要」，标注「待总结」并进入复核清单，不中断整份报告生成。

**Q：信息不足一个月怎么办？** 见 §8.1：主窗口 + 补充窗口回溯 30 天，缺口单列。

**Q：会编造数据吗？** 见 §10：约束 + 兜底，不构成零幻觉保证，最终由人工复核兜底。

---

## 18. License 与贡献

- **License**：待定（建议在代码首次提交前选定，如 MIT / Apache-2.0，需与合规团队确认）。
- **远程仓库**：`git clone <repo-url>` 中的 `<repo-url>` 待建立远程仓库后补充。
- **贡献**：贡献指南待补充；PR 需通过 §13 的 lint + 单测 + 依赖安全扫描。
