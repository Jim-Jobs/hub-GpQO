#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
统计与洞察计算

输入 projects.json, 输出 stats.json。所有统计默认基于"核心样本"
(相关性 high/medium 且 性质为服务采购), 因为这是投标方真正要抢的盘子;
工程建设类和会议活动类单独统计, 避免拉偏单价基准。

用法:
  python analyze.py --data ./esg_bid_data
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REF = Path(__file__).parent.parent / "references" / "taxonomy.json"

# 热门标的主题词表。数组顺序即优先级: 每个项目只归入第一个命中的主题,
# 保证各主题互斥、分布可加。泛词("双碳")放最后作兜底, 否则会吃掉全部样本。
TOPIC_RULES = [
    ("温室气体核算核查", ["碳核查", "碳盘查", "温室气体", "碳排放核算", "碳数据", "碳监测"]),
    ("碳足迹/碳标签", ["碳足迹", "碳标签", "生命周期评价"]),
    ("ESG报告/信息披露", ["ESG报告", "社会责任报告", "可持续发展报告", "可持续披露", "信息披露", "CSR"]),
    ("ESG评价/评级", ["ESG评价", "ESG评级", "ESG评估", "ESG指标体系", "ESG管理", "ESG"]),
    ("碳资产/碳交易", ["碳资产", "碳交易", "CCER", "碳金融", "碳普惠", "碳市场"]),
    ("零碳/低碳载体", ["零碳", "近零碳", "低碳园区", "低碳工厂", "示范园区", "先导区", "示范试点"]),
    ("双碳平台/数字化", ["平台", "系统", "数字化", "数据库", "信息化", "智慧"]),
    ("节能诊断/能源审计", ["节能诊断", "能源审计", "节能评估", "能效"]),
    ("绿色工厂/供应链", ["绿色工厂", "绿色供应链", "绿色制造"]),
    ("绿色金融/气候投融资", ["绿色金融", "转型金融", "气候投融资", "气候风险", "TCFD"]),
    ("能力建设/培训", ["能力提升", "能力建设", "培训", "研修"]),
    ("双碳规划/路径研究", ["双碳", "碳达峰", "碳中和", "路径", "行动方案", "十五五", "实施方案"]),
]
TOPIC_FALLBACK = "其他 ESG/绿色服务"


def pct(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo))


def summarize(vals: list[float]) -> dict:
    s = sorted(v for v in vals if v)
    if not s:
        return {"n": 0, "total": 0, "mean": None, "median": None,
                "p25": None, "p75": None, "min": None, "max": None}
    return {
        "n": len(s), "total": round(sum(s), 2),
        "mean": round(sum(s) / len(s), 2),
        "median": round(pct(s, 0.5), 2),
        "p25": round(pct(s, 0.25), 2),
        "p75": round(pct(s, 0.75), 2),
        "min": round(s[0], 2), "max": round(s[-1], 2),
    }


def bucket_of(amount: float, buckets: list[dict]) -> str:
    for b in buckets:
        if amount >= b["min"] and (b["max"] is None or amount < b["max"]):
            return b["label"]
    return buckets[-1]["label"]


def main():
    ap = argparse.ArgumentParser(description="统计与洞察")
    ap.add_argument("--data", default="./esg_bid_data")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    projects = json.loads((data / "projects.json").read_text(encoding="utf-8"))
    tax = json.loads(REF.read_text(encoding="utf-8"))
    buckets = tax["unit_axis"]["buckets"]

    core = [p for p in projects
            if p["relevance"] in ("high", "medium") and p["nature"] == "service"]
    core_awarded = [p for p in core if p["status"] == "awarded"]
    core_priced = [p for p in core_awarded if p["amount_yuan"]]

    stats: dict = {"meta": {
        "generated_at": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
        "n_projects_all": len(projects),
        "n_core": len(core),
        "n_core_awarded": len(core_awarded),
        "n_core_priced": len(core_priced),
        "amount_coverage": round(len(core_priced) / max(len(core_awarded), 1) * 100, 1),
    }}

    # ---- 总量
    stats["overview"] = summarize([p["amount_yuan"] for p in core_priced])
    stats["overview"]["n_total_tenders"] = len(core)

    # ---- 年度趋势
    by_year = defaultdict(lambda: {"count": 0, "amount": 0.0, "n_amount": 0})
    for p in core:
        y = p.get("year") or ""
        if not y:
            continue
        by_year[y]["count"] += 1
        if p["status"] == "awarded" and p["amount_yuan"]:
            by_year[y]["amount"] += p["amount_yuan"]
            by_year[y]["n_amount"] += 1
    stats["by_year"] = {k: {"count": v["count"], "amount": round(v["amount"], 2),
                            "n_amount": v["n_amount"]}
                        for k, v in sorted(by_year.items())}

    # ---- 月度趋势
    by_month = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for p in core:
        d = p.get("award_date") or p.get("first_date") or ""
        if len(d) >= 7:
            m = d[:7]
            by_month[m]["count"] += 1
            if p["amount_yuan"] and p["status"] == "awarded":
                by_month[m]["amount"] += p["amount_yuan"]
    stats["by_month"] = {k: {"count": v["count"], "amount": round(v["amount"], 2)}
                         for k, v in sorted(by_month.items())}

    # ---- 金额分档
    dist = {b["label"]: {"count": 0, "amount": 0.0} for b in buckets}
    for p in core_priced:
        lb = bucket_of(p["amount_yuan"], buckets)
        dist[lb]["count"] += 1
        dist[lb]["amount"] += p["amount_yuan"]
    stats["amount_dist"] = [
        {"label": k, "count": v["count"], "amount": round(v["amount"], 2),
         "share": round(v["count"] / max(len(core_priced), 1) * 100, 1)}
        for k, v in dist.items()
    ]

    # ---- 分层结构
    tier_label = {k: v["label"] for k, v in tax["tiers"].items()}
    tier_agg = defaultdict(lambda: {"count": 0, "amount": 0.0, "priced": []})
    for p in core:
        t = p["tier"]
        tier_agg[t]["count"] += 1
        if p["status"] == "awarded" and p["amount_yuan"]:
            tier_agg[t]["amount"] += p["amount_yuan"]
            tier_agg[t]["priced"].append(p["amount_yuan"])
    stats["tier_dist"] = [
        {"tier": t, "label": tier_label.get(t, t), "count": v["count"],
         "amount": round(v["amount"], 2),
         "median": round(pct(sorted(v["priced"]), 0.5), 2) if v["priced"] else None,
         "priced_n": len(v["priced"])}
        for t, v in sorted(tier_agg.items())
    ]

    # ---- 行业
    ind = defaultdict(lambda: {"count": 0, "amount": 0.0, "priced": []})
    for p in core:
        ind[p["industry"]]["count"] += 1
        if p["status"] == "awarded" and p["amount_yuan"]:
            ind[p["industry"]]["amount"] += p["amount_yuan"]
            ind[p["industry"]]["priced"].append(p["amount_yuan"])
    stats["industry_dist"] = sorted(
        [{"name": k, "count": v["count"], "amount": round(v["amount"], 2),
          "median": round(pct(sorted(v["priced"]), 0.5), 2) if v["priced"] else None}
         for k, v in ind.items()],
        key=lambda x: -x["count"])

    # ---- 地域
    reg = defaultdict(lambda: {"count": 0, "amount": 0.0})
    for p in core:
        r = p["region"] or "未知"
        reg[r]["count"] += 1
        if p["status"] == "awarded" and p["amount_yuan"]:
            reg[r]["amount"] += p["amount_yuan"]
    stats["region_dist"] = sorted(
        [{"name": k, "count": v["count"], "amount": round(v["amount"], 2)}
         for k, v in reg.items()], key=lambda x: -x["amount"])[:15]

    # ---- 竞争格局
    sup = defaultdict(lambda: {"count": 0, "amount": 0.0, "priced": [],
                               "regions": Counter(), "tiers": Counter(), "buyers": set()})
    for p in core_awarded:
        w = (p.get("winner") or "").strip()
        if not w or len(w) < 4:
            continue
        s = sup[w]
        s["count"] += 1
        s["regions"][p["region"] or "未知"] += 1
        s["tiers"][p["tier"]] += 1
        if p["buyer"]:
            s["buyers"].add(p["buyer"])
        if p["amount_yuan"]:
            s["amount"] += p["amount_yuan"]
            s["priced"].append(p["amount_yuan"])
    total_amt = sum(v["amount"] for v in sup.values()) or 1
    stats["suppliers"] = sorted([
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2),
         "share": round(v["amount"] / total_amt * 100, 1),
         "avg": round(v["amount"] / len(v["priced"]), 2) if v["priced"] else None,
         "median": round(pct(sorted(v["priced"]), 0.5), 2) if v["priced"] else None,
         "regions": [r for r, _ in v["regions"].most_common(3)],
         "tier": v["tiers"].most_common(1)[0][0] if v["tiers"] else "",
         "n_buyers": len(v["buyers"])}
        for k, v in sup.items()], key=lambda x: (-x["count"], -x["amount"]))[:20]

    # ---- 采购方复购
    buy = defaultdict(lambda: {"count": 0, "amount": 0.0, "projects": []})
    for p in core:
        b = p["buyer"] or "未知"
        buy[b]["count"] += 1
        if p["amount_yuan"]:
            buy[b]["amount"] += p["amount_yuan"]
        buy[b]["projects"].append(p["project_name"][:40])
    stats["buyers"] = sorted([
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2),
         "sample": v["projects"][0]}
        for k, v in buy.items()], key=lambda x: (-x["count"], -x["amount"]))[:15]

    # ---- 热门标的主题 (单归属, 互斥可加)
    def topic_of(name: str) -> str:
        low = (name or "").lower()
        for label, words in TOPIC_RULES:
            if any(w.lower() in low for w in words):
                return label
        return TOPIC_FALLBACK

    tpidx = defaultdict(lambda: {"count": 0, "amount": 0.0, "priced": []})
    for p in core:
        t = topic_of(p["project_name"])
        tpidx[t]["count"] += 1
        if p["status"] == "awarded" and p["amount_yuan"]:
            tpidx[t]["amount"] += p["amount_yuan"]
            tpidx[t]["priced"].append(p["amount_yuan"])
    stats["topics"] = sorted([
        {"name": k, "count": v["count"], "amount": round(v["amount"], 2),
         "median": round(pct(sorted(v["priced"]), 0.5), 2) if v["priced"] else None,
         "priced_n": len(v["priced"])}
        for k, v in tpidx.items()], key=lambda x: -x["amount"])

    # ---- 价格基准 (按分层)
    bench = {}
    for t, v in tier_agg.items():
        if v["priced"]:
            s = sorted(v["priced"])
            bench[tier_label.get(t, t)] = {
                "n": len(s), "p25": round(pct(s, 0.25), 2),
                "median": round(pct(s, 0.5), 2), "p75": round(pct(s, 0.75), 2),
            }
    stats["benchmark"] = bench

    # ---- 高价值项目
    stats["top_deals"] = [
        {"name": p["project_name"], "buyer": p["buyer"], "region": p["region"],
         "winner": p["winner"], "amount": p["amount_yuan"], "date": p["award_date"],
         "tier": tier_label.get(p["tier"], p["tier"]), "url": p["detail_url"]}
        for p in sorted(core_priced, key=lambda x: -x["amount_yuan"])[:15]
    ]

    (data / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=1),
                                     encoding="utf-8")

    ov = stats["overview"]
    print(f"核心样本 {len(core)} 个 / 成交 {len(core_awarded)} 个 / 含金额 {len(core_priced)} 个")
    if ov["total"]:
        print(f"成交总额 {ov['total']/1e8:.3f} 亿 | 中位数 {ov['median']/1e4:.1f} 万 | "
              f"均值 {ov['mean']/1e4:.1f} 万")
    print(f"分层: {[(x['label'], x['count']) for x in stats['tier_dist']]}")
    print(f"Top主题: {[(x['name'], x['count']) for x in stats['topics'][:5]]}")
    print(f"-> {data / 'stats.json'}")


if __name__ == "__main__":
    main()
