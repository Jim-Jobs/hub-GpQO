---
name: esg-bid-intelligence
description: 采集并分析中国 ESG / 可持续发展 / 双碳领域的招投标公开数据，产出市场规模、热点标的、金额分布、竞争格局与投标价格基准的可视化报告，并支持每日新增招标监控推送。当用户需要"汇总某段时间的 ESG/双碳中标情况"、"做招投标市场分析"、"看某个领域谁在花钱/谁在拿单"、"监控每天的新招标信息"、"生成投标商机报告"时使用。
agent_created: true
---

# ESG 投标情报分析

从中国政府采购网公开公告中采集 ESG、可持续发展、双碳相关项目的招标与中标数据，
清洗打标后产出可交付的市场情报报告与每日监控简报。

## 何时使用

- 汇总过去 N 年 ESG/双碳/可持续发展领域的中标情况，分析热点方向与金额分布
- 摸清某个赛道（如碳核查、ESG 报告编制、零碳园区）的买方结构与竞争格局
- 为投标报价寻找价格基准（P25/中位数/P75）
- 每日监控新发布的 ESG 相关招标，推送到本地

## 环境准备

脚本依赖 `playwright`（Chromium 内核）、`bs4`、`lxml`、`jinja2`、`openpyxl`。
默认解释器用 conda 的 py312。首次使用先确认：

```bash
python "<SKILL_DIR>/scripts/ccgp_crawler.py" --mode history \
  --start 2025-01-01 --end 2025-12-31 --keywords "ESG" --max-pages 1 \
  --no-detail --out ./esg_bid_data
```

能打印出条数即环境正常。

## 工作流 A：历史市场分析

### A1. 先探路，确认规模

先只抓列表、不抓详情，几十秒即可知道样本量级：

```bash
python "<SKILL_DIR>/scripts/ccgp_crawler.py" --mode history \
  --start 2024-11-01 --end 2026-11-24 --no-detail --out ./esg_bid_data
```

输出会打印去重后的公告数。若数量为数百到数千条，进入 A2。

### A2. 补抓详情（耗时较长，可中断续跑）

列表抓完后**务必加 `--from-cache`**，直接复用 A1 的结果进入详情阶段，
否则会重新跑一遍几十分钟的列表检索：

```bash
python "<SKILL_DIR>/scripts/ccgp_crawler.py" --mode history \
  --start 2024-11-01 --end 2026-11-24 --out ./esg_bid_data --from-cache
```

只对中标/成交/结果类公告抓详情（默认行为），命中缓存自动跳过，中断后重跑即续。
实测速率约 25–30 条/分钟，700 条约 25 分钟。长时间运行请放到后台任务。
输出 `raw/notices_<起>_<止>.json`。

### A3. 清洗、分析、渲染

**推荐一键命令**（自动找 `raw/` 下最新的 `notices_*.json`）：

```bash
python "<SKILL_DIR>/scripts/pipeline.py" history \
  --data ./esg_bid_data --period "2024-11-01 ~ 2026-11-24"
```

需要逐步排查时再手动跑三步：

```bash
python "<SKILL_DIR>/scripts/normalize.py" --raw ./esg_bid_data/raw/notices_<起>_<止>.json --out ./esg_bid_data
python "<SKILL_DIR>/scripts/analyze.py"   --data ./esg_bid_data
python "<SKILL_DIR>/scripts/render_report.py" --data ./esg_bid_data --period "2024-11-01 ~ 2026-11-24"
```

`pipeline.py status --data ./esg_bid_data` 可查看各阶段产物是否齐全。

产出在 `./esg_bid_data/report/`：`esg_bid_report.html`（单文件可视化报告）、
`esg_bid_ledger.xlsx`（多 sheet 台账）、`esg_bid_brief.md`（速读版）。

### A4. 提升报告质量（重要）

渲染器自带规则化洞察，但**让报告真正像分析师写的最好方式，是模型先写一份洞察稿再喂进去**：

1. 先跑 `summarize.py` 生成洞察素材（把 stats.json 转成对比、集中度、单价差等判断素材，
   省去翻 JSON，也避免编数字）
2. 读素材，按 `references/narrative-guide.md` 的六段结构写 `insight.md`
3. 用 `--narrative ./insight.md` 重新渲染

```bash
python "<SKILL_DIR>/scripts/summarize.py" --data ./esg_bid_data --out ./insight_material.md
python "<SKILL_DIR>/scripts/pipeline.py" history --data ./esg_bid_data \
  --period "2024-11-01 ~ 2026-11-24" --narrative ./insight.md
```

写洞察稿的硬要求：**每个判断都要挂具体数字**，禁止"市场前景广阔""竞争激烈"这类无信息量表述；
金额、项目数、份额、中位数必须来自 stats.json，不得编造。

## 工作流 B：每日招标监控

```bash
python "<SKILL_DIR>/scripts/pipeline.py" daily --data ./esg_bid_data --days 1
# 等价于直接跑 daily.py
```

一条命令完成：抓当日 → 过滤 → 与已推送记录去重 → 生成 `briefs/YYYY-MM-DD.md` 与 `.html`。
`briefs/pushed.json` 记录已推送 id，同日重复运行不会重复推送；需要重新生成时加 `--force`。
漏跑几天用 `--days 3` 补齐。

若用户需要长期每日推送，用 automation 工具创建每日定时任务，prompt 指向上述命令。

## 脚本清单

| 脚本 | 职责 |
|---|---|
| `scripts/pipeline.py` | **一键编排**：`history` / `daily` / `status` 三个子命令 |
| `scripts/ccgp_crawler.py` | 抓取检索列表与详情正文，限速重试、断点续抓 |
| `scripts/amount_parser.py` | 从公告正文解析中标金额与中标供应商（多形态兼容） |
| `scripts/normalize.py` | 去噪、分层打标、判定项目性质、按项目聚合多公告 |
| `scripts/analyze.py` | 趋势、金额分布、行业地域、竞争格局、价格基准统计 |
| `scripts/summarize.py` | 把 stats.json 转成写洞察稿用的结构化素材（对比/集中度/单价差） |
| `scripts/render_report.py` | HTML 报告 / Excel 台账 / Markdown 简报 |
| `scripts/daily.py` | 每日增量监控与简报 |

## 口径与质量红线

- **三层口径（默认宽口径）**：L1 ESG 核心服务 / L2 双碳与碳管理 / L3 绿色低碳周边，
  定义见 `references/taxonomy.json`。默认把三层全部纳入核心样本（绿色工厂、节能诊断、
  气候投融资等周边赛道也算），报告内用 `tier_dist` 分层展示，读者可自行收窄。
  核心样本 = 「相关性高/中 且 性质为服务采购」；仅泛泛带"绿色低碳"字样、
  无具体服务标的的仍判 `low` 排除。
- **必须剔除关键词误命中**，两类最容易踩：
  1. 楼宇/设备名称带"双碳"字样的基建，例如"某大学'双碳'创新国际大厦（实验楼）
     项目造价咨询"、实训基地、设备采购、场地租赁、会议培训；
  2. **代理机构名污染**：ccgp 标题常写成「XX工程造价咨询有限公司关于YY双碳规划
     研究项目中标公告」，前缀里的"造价咨询/项目管理/监理"会把真实的咨询服务误判为工程。
     `normalize.py` 的 `strip_agent_prefix()` 负责切掉「…关于」前缀。
  另注意「温室气体排放清单编制」是核心碳核算服务，**不能**被"清单编制"误伤，
  工程信号只认「工程量清单/招标清单/控制价编制」。
  `HARD_ENGINEERING` 规则负责拦截，改动前先读 `references/playbook.md` 的"口径陷阱"一节。
- **不能把同项目的多条公告重复计费**。招标公告、更正公告、中标公告必须聚合成一个项目。
- **金额覆盖率**要如实披露。公告未披露中标价的情况很常见，覆盖率低于 80% 时
  必须在报告里说明绝对金额可能低估。

## 常见问题

- **报错"访问过于频繁"**：ccgp 的 WAF 触发限流。把 `--min-delay/--max-delay` 调大到
  3/5 秒，脚本本身有退避重试，通常等 10–30 秒即可恢复。不要并发抓取。
- **裸 requests 请求返回 403**：正常，ccgp 对非浏览器 UA 直接拒绝，必须走 Playwright。
- **某关键词命中 0 条**：站内检索是标题检索，长尾词召回极低。从
  `references/taxonomy.json` 的 `search_keywords` 里换词，不要用超长短语。
- **报告里图表空白**：`assets/echarts.min.js` 缺失。该文件需随 skill 一起分发，
  缺失时模板会退回 CDN，但国内网络常加载失败。

技术细节与踩坑记录见 `references/playbook.md`。
