#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
洞察素材摘要: 把 stats.json / projects.json 转成便于撰写 insight.md 的结构化素材。

analyze.py 的 stats.json 是给图表用的; 写洞察稿需要的是"对比与判断",
例如单价差几倍、CR3 多少、哪个赛道高频低价、哪年数据不完整。
本脚本把这些对比预先算好, 输出 Markdown, 模型据此写稿可避免翻 JSON 和编造数字。

用法:
  python summarize.py --data ./esg_bid_data
  python summarize.py --data ./esg_bid_data --out ./insight_material.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def wan(v, nd=1) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e8:
        return f"{v/1e8:.2f}亿"
    return f"{v/1e4:,.{nd}f}万"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成洞察素材摘要")
    ap.add_argument("--data", default="./esg_bid_data")
    ap.add_argument("--out", default=None, help="省略则打印到 stdout")
    ap.add_argument("--period", default="", help="统计区间，缺省从项目日期推断")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    stats = json.loads((data / "stats.json").read_text(encoding="utf-8"))
    projects = json.loads((data / "projects.json").read_text(encoding="utf-8"))

    meta, ov = stats["meta"], stats["overview"]
    # render_report.py 把 period 写进内存但未回写 stats.json, 这里从项目日期兜底推断
    period = args.period or meta.get("period", "")
    if not period:
        dates = sorted((p.get("award_date") or p.get("first_date") or "")
                       for p in projects if (p.get("award_date") or p.get("first_date")))
        if dates:
            period = f"{dates[0]} ~ {dates[-1]}"
    L: list[str] = []
    A = L.append

    A("# 洞察素材（数字均来自 stats.json，可直接引用）")
    A("")
    A(f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}　统计区间：{period}")
    A(f"- 数据源：中国政府采购网公开公告（不含国企/央企自主采购平台）")
    A("")

    A("## 1. 基本盘")
    A("")
    A(f"| 指标 | 数值 |")
    A(f"|---|---|")
    A(f"| 核心项目数 | {meta['n_core']} 个 |")
    A(f"| 已成交 | {meta['n_core_awarded']} 个 |")
    A(f"| 披露金额 | {meta['n_core_priced']} 个（覆盖率 {meta['amount_coverage']}%）|")
    A(f"| 成交总额 | {wan(ov.get('total'))}（仅已披露部分，属下限）|")
    A(f"| 中位单价 | {wan(ov.get('median'))} |")
    A(f"| 平均单价 | {wan(ov.get('mean'))} |")
    A(f"| P25 / P75 | {wan(ov.get('p25'))} / {wan(ov.get('p75'))} |")
    A(f"| 最小 / 最大 | {wan(ov.get('min'))} / {wan(ov.get('max'))} |")
    if ov.get("mean") and ov.get("median") and ov["mean"] > ov["median"] * 1.2:
        A("")
        A(f"> 均值是中位的 {ov['mean']/ov['median']:.2f} 倍，金额分布右偏，少数大额项目拉高整体。")
    A("")

    A("## 2. 时间结构（注意区间截断）")
    A("")
    A("| 年份 | 项目数 | 成交额 | 含金额数 |")
    A("|---|---|---|---|")
    for y in sorted(stats["by_year"]):
        v = stats["by_year"][y]
        A(f"| {y} | {v['count']} | {wan(v['amount'])} | {v.get('n_amount','—')} |")
    A("")
    A("> 首尾年份可能只覆盖部分月份，不可直接同比；跨年趋势以月度走势为准。")
    by_month = stats.get("by_month", {})
    if by_month:
        top_m = sorted(by_month.items(), key=lambda kv: -kv[1]["amount"])[:3]
        A(f"> 成交额最高的 3 个月："
          + "、".join(f"{k}（{wan(v['amount'])}，{v['count']} 个）" for k, v in top_m) + "。")
    A("")

    A("## 3. 三层口径（宽口径，互斥可加）")
    A("")
    A("| 分层 | 项目数 | 占比 | 成交额 | 中位单价 |")
    A("|---|---|---|---|---|")
    tot_n = sum(x["count"] for x in stats["tier_dist"]) or 1
    for t in stats["tier_dist"]:
        A(f"| {t['label']} | {t['count']} | {t['count']/tot_n*100:.1f}% | "
          f"{wan(t['amount'])} | {wan(t.get('median'))} |")
    A("")

    A("## 4. 热点标的（单归属）")
    A("")
    A("| 主题 | 项目数 | 成交额 | 中位单价 | 特征 |")
    A("|---|---|---|---|---|")
    tp = stats["topics"]
    med_all = ov.get("median") or 1
    for t in tp:
        tag = ("高单价少项目" if (t.get("median") or 0) > med_all * 1.5 and t["count"] < tot_n * 0.1
               else "低单价高频次" if t["count"] > tot_n * 0.15 and (t.get("median") or 0) < med_all
               else "—")
        A(f"| {t['name']} | {t['count']} | {wan(t['amount'])} | {wan(t.get('median'))} | {tag} |")
    A("")

    A("## 5. 价格带")
    A("")
    A("| 价格带 | 项目数 | 占比 | 该带金额 |")
    A("|---|---|---|---|")
    for d in stats["amount_dist"]:
        A(f"| {d['label']} | {d['count']} | {d['share']}% | {wan(d.get('amount'))} |")
    A("")

    A("## 6. 买方结构")
    A("")
    A("| 行业 | 项目数 | 成交额 | 中位单价 |")
    A("|---|---|---|---|")
    for i in stats["industry_dist"]:
        A(f"| {i['name']} | {i['count']} | {wan(i['amount'])} | {wan(i.get('median'))} |")
    A("")
    A("地域 Top8：" + "、".join(
        f"{r['name']}({r['count']}个/{wan(r['amount'])})" for r in stats["region_dist"][:8]))
    A("")

    A("## 7. 竞争格局")
    A("")
    sups = stats["suppliers"]
    if sups:
        A(f"- CR3 = {sum(s['share'] for s in sups[:3]):.1f}%，"
          f"CR5 = {sum(s['share'] for s in sups[:5]):.1f}%")
        A(f"- 最高频机构：{sups[0]['name']}，{sups[0]['count']} 次、"
          f"{wan(sups[0]['amount'])}、份额 {sups[0]['share']}%、"
          f"覆盖 {len(sups[0].get('regions',[]))} 个地区")
        cr3 = sum(s["share"] for s in sups[:3])
        A("- 集中度判断：" + ("CR3 < 30%，市场分散，存在进入窗口"
                              if cr3 < 30 else
                              "CR3 30–50%，头部初现" if cr3 < 50 else
                              "CR3 > 50%，壁垒已成型"))
        A("")
        A("| 中标方 | 次数 | 金额 | 份额 | 区域 |")
        A("|---|---|---|---|---|")
        for s in sups[:10]:
            A(f"| {s['name'][:28]} | {s['count']} | {wan(s['amount'])} | {s['share']}% | "
              f"{'/'.join(s.get('regions',[])[:4]) or '—'} |")
    A("")

    A("## 8. 分层价格基准（投标报价锚点）")
    A("")
    A("| 分层 | 样本 | P25 | 中位 | P75 |")
    A("|---|---|---|---|---|")
    for k, b in stats["benchmark"].items():
        A(f"| {k} | {b['n']} | {wan(b['p25'])} | {wan(b['median'])} | {wan(b['p75'])} |")
    if stats["benchmark"]:
        b0 = max(stats["benchmark"].items(), key=lambda kv: kv[1]["n"])
        A("")
        A(f"> 样本量最大的分层是「{b0[0]}」（n={b0[1]['n']}），"
          f"报价锚点建议参考其中位 {wan(b0[1]['median'])}，"
          f"高于 P75（{wan(b0[1]['p75'])}）需有差异化理由。")
    A("")

    A("## 9. 大额项目（案例佐证）")
    A("")
    A("| 项目 | 中标方 | 金额 | 地区 |")
    A("|---|---|---|---|")
    for d in stats.get("top_deals", [])[:10]:
        A(f"| {d['name'][:44]} | {(d['winner'] or '—')[:22]} | {wan(d['amount'])} | {d['region']} |")
    A("")

    A("## 10. 写稿前必读的口径约束")
    A("")
    A(f"- 金额覆盖率 {meta['amount_coverage']}%，未披露金额的项目不计入总额，"
      "绝对金额是**下限**，趋势与结构结论更可靠。")
    A("- 政府/事业单位占比高是 ccgp 数据源特性（政府采购网），不能外推到全市场。")
    A("- 首尾年份非完整自然年，禁止写「同比下滑/增长」；用月度走势或完整年做对比。")
    A("- 每个判断挂具体数字；禁止「前景广阔」「竞争激烈」「值得关注」等空话。")
    A("- 样本量 < 5 的分层或主题，明确标注「样本不足，仅供参考」。")
    A("")
    A(f"<!-- 项目明细共 {len(projects)} 条，见 projects.json -->")

    text = "\n".join(L)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"-> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
