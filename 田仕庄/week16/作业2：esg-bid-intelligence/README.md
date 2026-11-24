# ESG 投标情报分析（esg-bid-intelligence）

基于中国政府采购网（ccgp.gov.cn）公开公告，采集 ESG / 可持续发展 / 双碳相关招标与中标数据，
清洗打标后产出市场情报报告与每日监控简报。**纯公开免费数据源，无需任何账号或 API Key。**

## 能力

- **历史市场分析**：热点赛道、中标金额分布、买方 / 地域 / 行业结构、竞争格局、投标报价基准（P25 / 中位数 / P75）
- **每日招标监控**：抓取当日新增公告、与历史去重、输出 Markdown / HTML 简报
- **交付物**：单文件 HTML 可视化报告（内嵌 ECharts）、多 sheet Excel 台账、Markdown 速读版

## 目录结构

```
esg-bid-intelligence/
├── SKILL.md                     # Skill 定义、工作流、口径红线
├── README.md                    # 本文件
├── requirements.txt             # Python 依赖
├── .gitignore
├── scripts/
│   ├── pipeline.py              # 一键编排：history / daily / status
│   ├── ccgp_crawler.py          # 列表 + 详情抓取（限速、重试、断点续抓）
│   ├── amount_parser.py         # 中标金额 / 供应商解析
│   ├── normalize.py             # 去噪、分层打标、项目聚合
│   ├── analyze.py               # 统计与价格基准
│   ├── summarize.py             # 生成洞察稿素材
│   ├── render_report.py         # HTML / Excel / Markdown 渲染
│   └── daily.py                 # 每日增量监控
├── references/
│   ├── taxonomy.json            # 三层口径 + 20 个检索关键词
│   ├── playbook.md              # 踩坑记录（WAF、标题检索、跨年、金额写法、口径陷阱）
│   └── narrative-guide.md       # 洞察稿六段结构规范
└── assets/
    ├── report_template.html
    └── echarts.min.js           # 必须随包分发，缺失会导致报告图表空白
```

> `scripts/` 内各模块存在互相 import，8 个 .py 必须保持同目录，不能拆分上传。

## 环境准备

Python 3.12（推荐 conda 环境 `py312`）：

```bash
pip install -r requirements.txt
playwright install chromium      # 关键：ccgp 有 WAF，必须走真实浏览器内核
```

只装 pip 依赖不够，Chromium 内核是单独下载的（约 100+ MB）。验证环境：

```bash
python scripts/ccgp_crawler.py --mode history \
  --start 2025-01-01 --end 2025-12-31 --keywords "ESG" \
  --max-pages 1 --no-detail --out ./esg_bid_data
```

能打印出条数即环境正常。

## 快速开始

所有命令在**仓库根目录**执行；`--data ./esg_bid_data` 是数据工作目录，不入库。

### A. 历史市场分析

```bash
# A1 只抓列表，先摸清样本量（几十秒）
python scripts/ccgp_crawler.py --mode history \
  --start 2024-11-01 --end 2026-11-24 --no-detail --out ./esg_bid_data

# A2 补抓详情：务必加 --from-cache，否则会重跑几十分钟的列表检索
python scripts/ccgp_crawler.py --mode history \
  --start 2024-11-01 --end 2026-11-24 --out ./esg_bid_data --from-cache

# A3 清洗 → 分析 → 渲染
python scripts/pipeline.py history --data ./esg_bid_data \
  --period "2024-11-01 ~ 2026-11-24"

# A4（推荐）先写洞察稿再渲染，报告质量明显更高
python scripts/summarize.py --data ./esg_bid_data --out ./insight_material.md
python scripts/pipeline.py history --data ./esg_bid_data \
  --period "2024-11-01 ~ 2026-11-24" --narrative ./insight.md
```

`python scripts/pipeline.py status --data ./esg_bid_data` 可检查各阶段产物是否齐全。
写洞察稿的硬要求：每个判断都要挂 `stats.json` 里的具体数字，不得编造。

### B. 每日监控

```bash
python scripts/pipeline.py daily --data ./esg_bid_data --days 1
# 漏跑几天：--days 3；同日重生成：--force；跳过抓取直接重渲染：--no-crawl
```

产物在 `esg_bid_data/briefs/YYYY-MM-DD.md` 与 `.html`，`pushed.json` 记录已推送 id，避免重复推送。
长期每日运行需在目标环境用平台定时任务（如 WorkBuddy automation）指向上述命令——
**定时任务配置不随代码分发，换环境必须重建**。

## 产物与数据（默认不随代码提交）

| 路径 | 说明 |
|---|---|
| `esg_bid_data/raw/`、`cache/` | 抓取原始数据与详情缓存，约 26 MB / 2000+ 小文件，可由脚本重跑生成 |
| `esg_bid_data/projects.json` / `stats.json` | 清洗与统计中间结果 |
| `esg_bid_data/report/` | 最终交付：HTML 报告、Excel 台账、Markdown 简报 |
| `esg_bid_data/briefs/` | 每日简报 |

以上均已写入 `.gitignore`。若希望别人 clone 后直接看到成果，注释掉对应规则并把 `report/` 加入提交。

## 口径与边界

- **三层宽口径**：L1 ESG 核心服务 / L2 双碳与碳管理 / L3 绿色低碳周边，定义见 `references/taxonomy.json`，报告内分层展示。
- **必须剔除**：楼宇基建工程、设备货物采购、场地租赁、会议培训、能源费用托管、试验室 / 台架采购等关键词误命中；同一项目的招标 / 更正 / 中标公告必须聚合，避免重复计费。
- **金额是下限**：仅覆盖政府采购公开渠道，未披露中标价的公告不计入；报告如实披露金额覆盖率。
- **首尾年份非完整自然年时不做年度同比**；结论以趋势与结构为主，绝对金额不宜当作全市场规模。

## 常见问题

- **"访问过于频繁"**：触发 ccgp WAF，把 `--min-delay/--max-delay` 调大到 3/5 秒，脚本自带退避重试；不要并发抓取。
- **裸 requests 403**：正常，ccgp 拒绝非浏览器 UA，必须走 Playwright。
- **某关键词 0 条**：站内是标题检索，长尾词召回极低，从 `taxonomy.json` 的 `search_keywords` 换词。
- **报告图表空白**：`assets/echarts.min.js` 未随包分发，国内 CDN 常加载失败。
- 更多技术细节见 `references/playbook.md`。

## 免责声明

数据全部来自公开政府采购公告，仅用于投标商机分析与市场研究；抓取请遵守目标站点的访问频率与 robots 约定。
