# 煤炭 / 煤电行业 ESG 关键词与 Query 库

> 用途：维护 `config/client_shenhua.yaml` 的 `keywords` 与 `config/sources.yaml` 的 `queries`（README §7.1、§7.2）。
> 占位符：`{month}`（YYYY-MM）、`{client}`（客户名）、`{peer}`（对标企业名），由 `run.py` 展开；`{peer}` 会对 `peers.domestic` 逐家展开。
> 本库是候选池：真正生效的是两份配置文件，新增词先在这里验证效果。

## 1. 议题关键词库（按 E / S / G）

**E（环境）**：双碳、碳达峰、碳中和、碳排放、碳配额、碳市场、CCER、温室气体、甲烷、瓦斯抽采利用、绿色矿山、生态修复、土地复垦、矿井水、煤矸石、采煤沉陷区、能耗双控、节能降碳、超低排放、三改联动、灵活性改造、供热改造、煤电低碳化改造、掺烧、生物质、绿氨、CCUS、碳捕集、氢能、光伏、风电、源网荷储、转型金融、绿色债券

**S（社会）**：安全生产、煤矿安全、瓦斯突出、冲击地压、透水、矿工权益、职业病、尘肺病、能源保供、电煤长协、职工培训、乡村振兴、社区共建、搬迁安置

**G（治理）**：ESG 治理、董事会、可持续发展委员会、信息披露、内控、合规、反腐败、关联交易、高管薪酬、股东回报、独立董事

**评级与披露**：MSCI ESG 评级、标普 CSA、CDP、华证 ESG、中证 ESG、Wind ESG、ISSB、GRI、可持续发展报告、ESG 报告

**对标企业**：陕西煤业、中煤能源、兖矿能源、Peabody、Glencore

> 相关性打分按关键词命中数计算（命中数 ÷ 2，上限 1.0，见 [rank_filter.py](../scripts/rank_filter.py)），词表宁精勿滥：宽泛词（如单独一个「煤炭」）会稀释区分度。

## 2. Query 模板库（对应 `sources.yaml` 五类分组）

**policy（政策）**

- `{month} ESG 政策 生态环境部`
- `{month} 碳排放 政策 发改委`
- `{month} 上市公司 ESG 信息披露 交易所`
- `{month} 能源保供 政策 国家能源局`
- `{month} 绿色金融 转型金融 政策`

**standard（评级 / 标准）**

- `{month} ISSB GRI 评级 机构 动态`
- `{month} MSCI ESG 评级 方法 更新`
- `{month} 可持续发展报告 准则 财政部`
- `{month} sustainable reporting standard update`

**industry（行业）**

- `{month} 煤炭 行业 动态`
- `{month} 煤电 一体化 政策`
- `{month} 绿色矿山 名录`
- `{month} 煤矿 安全生产 事故`
- `{month} 煤电 低碳化 改造`
- `{month} 碳市场 配额 履约`
- `{month} China coal industry ESG`

**client（客户）**

- `{month} {client} ESG`
- `{month} {client} 公告`
- `{month} {client} 碳排放`
- `{month} {client} 安全生产`
- `{month} {client} 绿色矿山`

**peer（对标）**

- `{month} {peer} ESG`
- `{month} {peer} 绿色矿山`
- `{month} {peer} 碳排放`
- `{month} {peer} sustainability`（国际对标用英文）

## 3. 维护规则

- **双处生效**：关键词进客户 `keywords`（影响相关性打分）；query 进 `sources.yaml`（影响检索覆盖面）。两处都改才完整。
- **红线词不入库**：「预测」「走势」「涨价」「煤价预测」等不进 query，避免抓回与「不预测政策、不评论短期煤价」冲突的内容。
- **淘汰机制**：连续两个月零有效收录的 query 应替换或收紧措辞；只被低分来源命中的 query 检查是否过于宽泛。
- **英文补充**：国际对标与国际标准追踪优先用英文 query；境内政策以中文官方源为准。
- **新增客户**：复制 `client_shenhua.yaml` 后，按本库维度裁剪关键词，替换 `peers` 清单，再回填专属 query。