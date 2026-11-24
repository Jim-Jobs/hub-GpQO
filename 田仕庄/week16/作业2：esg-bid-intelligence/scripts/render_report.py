#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
报告渲染: 可视化 HTML + Excel 台账 + Markdown 简报

用法:
  python render_report.py --data ./esg_bid_data --out ./esg_bid_data/report
  python render_report.py --data ./esg_bid_data --narrative ./insight.md   # 用外部洞察稿

说明:
  narrative 由模型撰写时为最佳效果; 不提供则使用内置的规则化洞察生成器,
  保证任何情况下报告都有分析结论, 而不是只有图表。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SKILL_DIR = Path(__file__).parent.parent
TEMPLATE = SKILL_DIR / "assets" / "report_template.html"
REF = SKILL_DIR / "references" / "taxonomy.json"

sys.path.insert(0, str(Path(__file__).parent))
try:
    from analyze import TOPIC_FALLBACK, TOPIC_RULES
except Exception:  # 允许单独运行
    TOPIC_RULES, TOPIC_FALLBACK = [], ""

TIER_CN = {"L1_esg_core": "ESG核心服务", "L2_carbon": "双碳与碳管理", "L3_green": "绿色低碳周边"}
STATUS_CN = {"awarded": "已成交", "tender": "招标中", "failed": "废标/终止",
             "amended": "更正", "other": "其他"}


def topic_of(name: str) -> str:
    low = (name or "").lower()
    for label, words in TOPIC_RULES:
        if any(w.lower() in low for w in words):
            return label
    return TOPIC_FALLBACK


def wan(v, nd=1):
    if v is None:
        return "—"
    return f"{v/1e4:,.{nd}f}"


def _inline(s: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)


def md2html(md: str) -> str:
    """极简 Markdown -> HTML: 标题/粗体/段落/列表/引用/表格。

    洞察稿常带对比表格, 若不支持会把 `|` 原样输出成一堆竖线文本。
    """
    lines = md.split("\n")
    out: list[str] = []
    in_ul = False
    i = 0

    def close_ul():
        nonlocal in_ul
        if in_ul:
            out.append("</ul>")
            in_ul = False

    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            close_ul()
            i += 1
            continue

        # ---- Markdown 表格: 连续的 | 行, 第二行是 --- 分隔行
        if line.strip().startswith("|"):
            close_ul()
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i].strip())
                i += 1
            rows = [[c.strip() for c in r.strip("|").split("|")] for r in block]
            # 丢掉 |---|---| 这类分隔行
            data = [r for r in rows
                    if not all(re.fullmatch(r":?-{2,}:?", c or "") for c in r)]
            if data:
                th = "".join(f'<th style="padding:7px 10px;text-align:left;font-weight:600;'
                             f'color:#334155;background:#f1f5f9;border-bottom:1px solid #e2e8f0">'
                             f'{_inline(c)}</th>' for c in data[0])
                trs = []
                for r in data[1:]:
                    td = "".join(f'<td style="padding:7px 10px;border-bottom:1px solid #f1f5f9;'
                                 f'color:#475569">{_inline(c)}</td>' for c in r)
                    trs.append(f"<tr>{td}</tr>")
                out.append('<table style="width:100%;border-collapse:collapse;'
                           'margin:10px 0 16px;font-size:12.5px">'
                           f"<tr>{th}</tr>" + "".join(trs) + "</table>")
            continue

        if line.startswith("#"):
            close_ul()
            lvl = min(len(line) - len(line.lstrip("#")), 4)
            out.append(f"<h3>{_inline(line.lstrip('#').strip())}</h3>" if lvl <= 3
                       else f"<h4>{_inline(line.lstrip('#').strip())}</h4>")
        elif line.startswith(("- ", "* ")):
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append(f"<li>{_inline(line[2:].strip())}</li>")
        elif line.startswith(">"):
            close_ul()
            out.append(f'<div class="callout">{_inline(line.lstrip("> ").strip())}</div>')
        else:
            close_ul()
            out.append(f"<p>{_inline(line)}</p>")
        i += 1

    close_ul()
    return "\n".join(out)


def build_narrative(stats: dict, projects: list) -> str:
    """规则化洞察生成: 每个结论都必须由数据触发, 避免空话。"""
    ov = stats["overview"]
    parts: list[str] = []
    top_topics = stats["topics"][:3]
    sups = stats["suppliers"]
    dist = stats["amount_dist"]
    by_year = stats["by_year"]
    inds = stats["industry_dist"]
    regs = stats["region_dist"]
    bench = stats["benchmark"]
    period = stats["meta"].get("period", "")

    total_yi = (ov["total"] or 0) / 1e8
    parts.append("<h3>一、市场基本盘</h3>")
    s = (f"<p>本区间共识别 <strong>{stats['meta']['n_core']} 个</strong> ESG/双碳相关咨询服务采购项目，"
         f"其中 <strong>{stats['meta']['n_core_awarded']} 个</strong>已完成成交，"
         f"<strong>{stats['meta']['n_core_priced']} 个</strong>披露了成交金额，"
         f"合计 <strong>{total_yi:.3f} 亿元</strong>。")
    if ov["median"]:
        s += (f"中位单价 <strong>{ov['median']/1e4:.1f} 万元</strong>，"
              f"均值 {ov['mean']/1e4:.1f} 万元，"
              f"四分位区间 {ov['p25']/1e4:.1f}–{ov['p75']/1e4:.1f} 万元。")
    s += "均值高于中位数，说明金额分布右偏——少数大额项目拉高了整体水平。</p>" if (ov["mean"] or 0) > (ov["median"] or 0) * 1.2 else "</p>"
    parts.append(s)

    if len(by_year) >= 2:
        ks = sorted(by_year)
        a, b = by_year[ks[-2]], by_year[ks[-1]]
        # 首尾年份常不是完整自然年(如 2024-11 起、2026-11 止)。
        # 只有参与比较的两年都覆盖 1-12 月时, 同比才有意义, 否则会得出虚假的"回落"。
        pm = re.search(r"(\d{4})-(\d{2})-\d{2}\s*~\s*(\d{4})-(\d{2})-\d{2}", period or "")
        def complete(y: str) -> bool:
            if not pm:
                return False
            sy, sm, ey, em = pm.group(1), int(pm.group(2)), pm.group(3), int(pm.group(4))
            if y == sy:
                return sm == 1
            if y == ey:
                return em == 12
            return sy < y < ey
        if complete(ks[-1]) and complete(ks[-2]) and a["amount"]:
            chg = (b["amount"] - a["amount"]) / a["amount"] * 100
            trend = "增长" if chg > 0 else "回落"
            parts.append(
                f"<p>年度对比看，{ks[-1]} 年成交额较 {ks[-2]} 年{trend} "
                f"<strong>{abs(chg):.0f}%</strong>"
                f"（{a['amount']/1e4:,.0f} 万 → {b['amount']/1e4:,.0f} 万），"
                f"项目数由 {a['count']} 个变为 {b['count']} 个。"
                + ("规模扩张主要由项目数量驱动，说明需求端在持续放量。"
                   if b["count"] > a["count"] else
                   "项目数下降而金额上升，意味着单个项目变大、市场向高价值标的集中。")
                + "</p>")
        else:
            parts.append(
                f"<p><strong>年度口径提示：</strong>统计区间为 {period}，"
                f"首尾年份（{ks[0]}、{ks[-1]}）未覆盖完整自然年，"
                f"因此不做年度同比，年度柱状图仅表示各年在该区间内已披露公告的累计值。"
                f"跨年趋势请以月度走势为准。</p>")

    if top_topics:
        share_top = sum(t["amount"] for t in top_topics) / max(ov["total"] or 1, 1) * 100
        names = "、".join(t["name"] for t in top_topics)
        parts.append("<h3>二、热点标的</h3>")
        parts.append(
            f"<p>按成交额排序，最热的三个标的类型是 <strong>{names}</strong>，"
            f"合计占据 <strong>{share_top:.0f}%</strong> 的成交金额。"
            + (f"其中 <strong>{top_topics[0]['name']}</strong> 以 "
               f"{top_topics[0]['count']} 个项目、"
               f"{top_topics[0]['amount']/1e4:,.0f} 万元 "
               f"（中位 {wan(top_topics[0]['median'])} 万）居于首位。"
               if top_topics else "")
            + "</p>")
        if len(top_topics) > 1:
            t0, t1 = top_topics[0], top_topics[1]
            if t0.get("median") and t1.get("median"):
                if t0["median"] > t1["median"] * 1.8:
                    parts.append(
                        f"<p>值得注意的是，<strong>{t0['name']}</strong> 的中位单价"
                        f"（{wan(t0['median'])} 万）显著高于 <strong>{t1['name']}</strong>"
                        f"（{wan(t1['median'])} 万）。前者属于高单价、少项目的机会型赛道，"
                        f"后者是低单价、高频次的流量型赛道，二者对团队配置与销售打法的要求完全不同。</p>")

    parts.append("<h3>三、买方结构</h3>")
    if inds:
        # 只把有足够样本量的行业称作"主要来源", 单个项目不能代表结构性需求
        top = [i for i in inds if i["count"] >= 2][:3]
        if len(top) < 2:
            top = inds[:2]
        seg = "、".join(
            f"<strong>{i['name']}</strong>（{i['count']} 个" +
            (f"，中位 {wan(i['median'])} 万）" if i.get("median") else "）")
            for i in top)
        s = f"<p>采购方行业分布上，{seg}是主要需求来源。"
        if inds[0]["name"] in ("政府/事业", "国资平台/城投") \
                and inds[0]["count"] / stats["meta"]["n_core"] > 0.4:
            s += ("政府与国资体系是最稳定的付款方，预算来源明确、流程规范，"
                  "但也意味着资质门槛、评分办法和区域保护是必须提前拆解的变量。")
        if len(top) == 1:
            s += "需注意样本集中在单一行业，跨行业结论宜谨慎。"
        s += "</p>"
        parts.append(s)
    if regs:
        parts.append(
            f"<p>地域上，<strong>{regs[0]['name']}</strong>以 "
            f"{regs[0]['amount']/1e4:,.0f} 万元居首，"
            f"其后是 {regs[1]['name']}（{regs[1]['amount']/1e4:,.0f} 万）"
            f"与 {regs[2]['name']}（{regs[2]['amount']/1e4:,.0f} 万）。"
            if len(regs) >= 3 else ""
            + "头部省份多为经济活跃、双碳试点密集区域，本地化服务能力是拿单关键。</p>")

    parts.append("<h3>四、价格带与投标策略</h3>")
    if dist:
        peak = max(dist, key=lambda x: x["count"])
        big = sum(x["count"] for x in dist if x["label"] in ("100-300万", "300万以上"))
        low = sum(x["count"] for x in dist if x["label"] in ("10万以下", "10-30万"))
        n = stats["meta"]["n_core_priced"] or 1
        parts.append(
            f"<p>金额分布峰值落在 <strong>{peak['label']}</strong> 区间"
            f"（{peak['count']} 个项目，占 {peak['share']}%）。"
            f"100 万以上项目合计占 <strong>{big/n*100:.0f}%</strong>，"
            f"30 万以下小额项目占 {low/n*100:.0f}%。这与 ESG 咨询市场的两极结构吻合："
            f"一端是报告编制、专项核查等标准化低价服务，另一端是规划、平台建设、"
            f"示范区创建等百万级综合项目。</p>")
        parts.append(
            '<div class="callout"><b>投标含义：</b>若以标准化报告编制切入，'
            '需要接受 10–50 万的价格带并靠规模化摊薄成本；'
            '若要提升单客产值，必须往「规划+平台+创建」的组合型标的上走，'
            '这类项目对方法论沉淀和成功案例的要求更高，但一旦中标即形成区域标杆。</div>')

    parts.append("<h3>五、竞争格局</h3>")
    if sups:
        cr3 = sum(s["share"] for s in sups[:3])
        lead = sups[0]
        if lead["count"] >= 2:
            s = (f"<p>头部供应商中，<strong>{lead['name']}</strong> 以 "
                 f"{lead['count']} 次中标、{lead['amount']/1e4:,.0f} 万元"
                 f"（份额 {lead['share']}%）领先。")
        else:
            s = (f"<p>头部供应商中，<strong>{lead['name']}</strong> 以单笔 "
                 f"{lead['amount']/1e4:,.0f} 万元中标额居首（份额 {lead['share']}%），"
                 f"本次样本内尚无机构形成高频中标优势。")
        if len(sups) >= 3:
            s += (f"前三名合计份额 <strong>{cr3:.0f}%</strong>，"
                  + ("市场集中度较高，头部玩家已形成稳定的客户与区域壁垒，"
                     "新进入者宜从细分主题或下沉区域切入。"
                     if cr3 > 40 else
                     "市场较为分散，尚无绝对垄断者，是建立差异化定位的窗口期。"))
        s += "</p>"
        parts.append(s)
        multi = [x for x in sups if x["count"] >= 2]
        if len(multi) >= 2:
            parts.append(
                f"<p>出现多次中标的机构共 <strong>{len(multi)} 家</strong>，"
                f"这类复购型玩家通常绑定 2–3 个核心客户，"
                f"其打法值得作为对标样本复盘："
                f"{'、'.join(x['name'] for x in multi[:4])}。</p>")

    parts.append("<h3>六、行动建议</h3>")
    tips = []
    if top_topics:
        tips.append(f"锁定 <strong>{top_topics[0]['name']}</strong> 作为主攻方向，"
                    f"该赛道项目密度最高、客户认知成熟，最容易批量复制标书与交付模板。")
    if len(top_topics) > 1:
        tips.append(f"用 <strong>{top_topics[1]['name']}</strong> 做差异化补充，"
                    f"避开头部机构在主赛道的正面竞争。")
    if bench:
        # 取样本量最大的分层作为报价锚点, 避免用 2-3 个样本的小层做基准
        b_key, b0 = max(bench.items(), key=lambda kv: kv[1]["n"])
        if b0.get("median"):
            tips.append(f"报价锚定在 <strong>{wan(b0['median'])} 万元</strong>附近"
                        f"（{b_key}成交中位数，样本 {b0['n']} 个）；"
                        f"若报价高于 P75（{wan(b0['p75'])} 万），需有明确的差异化理由支撑。")
    if regs:
        tips.append(f"优先布局 <strong>{regs[0]['name']}</strong>，"
                    f"该地区成交额最高且项目密度大，值得设立本地交付力量。")
    tips.append("对发布时间在近 3 个月内的项目做二次跟进：中标结果公示后 6–12 个月是"
                "同一采购人启动下一期服务的高概率窗口。")
    parts.append("<ul>" + "".join(f"<li>{t}</li>" for t in tips) + "</ul>")

    return "\n".join(p for p in parts if p)


def render_html(stats, projects, out_path: Path, title: str, period: str,
                narrative_html: str):
    try:
        from jinja2 import Template
    except ImportError:
        raise SystemExit("需要 jinja2: pip install jinja2")

    ov = stats["overview"]
    sups = stats["suppliers"]
    total = ov["total"] or 1
    cr5 = sum(s["amount"] for s in sups[:5]) / total * 100

    tier_label = {t["tier"]: t["label"] for t in stats["tier_dist"]}
    suppliers = [{
        "name": s["name"], "count": s["count"],
        "amt_wan": f"{s['amount']/1e4:,.0f}",
        "share": f"{s['share']:.1f}",
        "avg_wan": wan(s["avg"]), "regions": "、".join(s["regions"]),
        "tier_label": tier_label.get(s["tier"], "—"),
    } for s in sups]

    deals = [{
        "name": d["name"], "buyer": d["buyer"], "winner": d["winner"],
        "amount_wan": wan(d["amount"]), "region": d["region"],
        "date": d["date"], "tier": d["tier"], "url": d["url"],
    } for d in stats.get("top_deals", [])]

    benchmarks = []
    for label, b in stats.get("benchmark", {}).items():
        benchmarks.append({"label": label, "n": b["n"],
                           "p25": wan(b["p25"]), "median": wan(b["median"]),
                           "p75": wan(b["p75"])})

    kpis = [
        {"label": "核心项目数", "value": stats["meta"]["n_core"], "unit": "个",
         "sub": "咨询服务类，剔除工程与活动", "cls": ""},
        {"label": "已成交", "value": stats["meta"]["n_core_awarded"], "unit": "个",
         "sub": f"金额披露率 {stats['meta']['amount_coverage']}%", "cls": ""},
        {"label": "成交总额", "value": f"{(ov['total'] or 0)/1e8:.3f}", "unit": "亿元",
         "sub": "仅含已披露金额项目", "cls": ""},
        {"label": "中位单价", "value": wan(ov["median"]), "unit": "万",
         "sub": f"P25–P75：{wan(ov['p25'])}–{wan(ov['p75'])}", "cls": ""},
        {"label": "平均单价", "value": wan(ov["mean"]), "unit": "万",
         "sub": "受大额项目拉高", "cls": ""},
        {"label": "头部集中度", "value": f"{cr5:.0f}", "unit": "%",
         "sub": "CR5，前五名金额份额", "cls": ""},
    ]

    tpl = Template(TEMPLATE.read_text(encoding="utf-8"))

    # 图表库内嵌: 报告需能在无网络环境下打开(国内访问 jsdelivr 常被重置),
    # 因此把 echarts 直接注入 HTML, 产出真正可单文件分发的报告。
    lib = SKILL_DIR / "assets" / "echarts.min.js"
    if lib.exists():
        echarts_js = lib.read_text(encoding="utf-8").replace("</script", "<\\/script")
    else:
        echarts_js = ('document.write(\'<script src="https://cdn.jsdelivr.net/npm/'
                      'echarts@5.5.0/dist/echarts.min.js"><\\/script>\');')

    html = tpl.render(
        title=title, period=period, source="中国政府采购网",
        meta=stats["meta"], kpis=kpis, suppliers=suppliers, deals=deals,
        benchmarks=benchmarks, narrative=narrative_html, echarts_js=echarts_js,
        data_json=json.dumps({
            "by_month": {k: {"count": v["count"], "amount": v["amount"]}
                         for k, v in stats["by_month"].items()},
            "by_year": stats["by_year"],
            "amount_dist": stats["amount_dist"],
            "tier_dist": stats["tier_dist"],
            "topics": stats["topics"],
            "industry_dist": stats["industry_dist"],
            "region_dist": stats["region_dist"],
            "benchmark": stats["benchmark"],
        }, ensure_ascii=False),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path


def render_excel(projects, stats, out_path: Path):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        print("  [跳过 Excel] 需要 openpyxl")
        return None

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="1E3A8A")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="E2E8F0")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def sheet(ws, headers, rows, widths):
        ws.append(headers)
        for c in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=c)
            cell.fill, cell.font = head_fill, head_font
            cell.alignment = Alignment(horizontal="center", vertical="center")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(rows)+1}"
        for row in ws.iter_rows(min_row=1, max_row=len(rows) + 1):
            for cell in row:
                cell.border = border
                if cell.row > 1:
                    cell.alignment = Alignment(vertical="top", wrap_text=False)

    core = [p for p in projects
            if p["relevance"] in ("high", "medium") and p["nature"] == "service"]
    core.sort(key=lambda p: -(p["amount_yuan"] or 0))

    ws = wb.active
    ws.title = "核心项目台账"
    sheet(ws,
          ["项目名称", "采购人", "行业", "地区", "分层", "主题", "状态",
           "中标金额(万元)", "中标单位", "公告数", "成交日期", "首次公告", "原文链接"],
          [[p["project_name"], p["buyer"], p["industry"], p["region"],
            TIER_CN.get(p["tier"], p["tier"]), topic_of(p["project_name"]),
            STATUS_CN.get(p["status"], p["status"]),
            round(p["amount_yuan"] / 1e4, 1) if p["amount_yuan"] else "",
            p["winner"], p["n_notices"], p["award_date"], p["first_date"],
            p["detail_url"]] for p in core],
          [56, 30, 14, 8, 16, 20, 10, 14, 30, 7, 12, 12, 46])

    ws2 = wb.create_sheet("全部项目")
    sheet(ws2,
          ["项目名称", "采购人", "地区", "分层", "性质", "相关性", "状态",
           "金额(万元)", "中标单位", "公告数", "日期", "链接"],
          [[p["project_name"], p["buyer"], p["region"],
            TIER_CN.get(p["tier"], p["tier"]), p["nature"], p["relevance"],
            STATUS_CN.get(p["status"], p["status"]),
            round(p["amount_yuan"] / 1e4, 1) if p["amount_yuan"] else "",
            p["winner"], p["n_notices"],
            p["award_date"] or p["first_date"], p["detail_url"]] for p in projects],
          [56, 30, 8, 16, 11, 8, 10, 13, 30, 7, 12, 46])

    ws3 = wb.create_sheet("供应商竞争")
    sheet(ws3,
          ["中标单位", "中标次数", "中标金额(万元)", "金额份额%", "平均单价(万元)",
           "中位单价(万元)", "覆盖区域", "服务客户数"],
          [[s["name"], s["count"], round(s["amount"] / 1e4, 1), s["share"],
            round(s["avg"] / 1e4, 1) if s["avg"] else "",
            round(s["median"] / 1e4, 1) if s["median"] else "",
            "、".join(s["regions"]), s["n_buyers"]] for s in stats["suppliers"]],
          [40, 10, 16, 12, 15, 15, 24, 12])

    ws4 = wb.create_sheet("采购方")
    sheet(ws4, ["采购人", "项目数", "金额(万元)", "代表项目"],
          [[b["name"], b["count"], round(b["amount"] / 1e4, 1), b["sample"]]
           for b in stats["buyers"]],
          [40, 9, 14, 50])

    ws5 = wb.create_sheet("主题分布")
    sheet(ws5, ["标的主题", "项目数", "成交金额(万元)", "中位单价(万元)"],
          [[t["name"], t["count"], round(t["amount"] / 1e4, 1),
            round(t["median"] / 1e4, 1) if t["median"] else ""]
           for t in stats["topics"]],
          [24, 9, 16, 15])

    ws6 = wb.create_sheet("口径说明")
    notes = [
        ["数据来源", "中国政府采购网 (ccgp.gov.cn) 公开公告"],
        ["统计区间", f"{stats['meta'].get('period', '')}"],
        ["生成时间", stats["meta"]["generated_at"]],
        ["核心样本", f"{stats['meta']['n_core']} 个（相关性高/中 且 项目性质为服务采购）"],
        ["金额覆盖率", f"{stats['meta']['amount_coverage']}%"],
        ["口径定义", "宽口径：ESG核心服务 + 双碳与碳管理 + 绿色低碳周边"],
        ["清洗规则", "剔除楼宇工程/设备采购/场地租赁/会议培训等关键词误命中；按采购人+项目名聚合多公告"],
        ["使用限制", "仅覆盖政府采购公开渠道，不含国企与央企自主采购平台，实际市场规模应更高"],
    ]
    sheet(ws6, ["项目", "说明"], notes, [16, 96])

    wb.save(out_path)
    return out_path


def render_markdown(stats, out_path: Path, title: str, period: str):
    ov = stats["overview"]
    lines = [
        f"# {title}", "",
        f"统计区间：{period}　|　数据来源：中国政府采购网　|　"
        f"生成时间：{stats['meta']['generated_at']}", "",
        "## 核心指标", "",
        "| 指标 | 数值 |", "|---|---|",
        f"| 核心项目数 | {stats['meta']['n_core']} 个 |",
        f"| 已成交项目 | {stats['meta']['n_core_awarded']} 个 |",
        f"| 成交总额 | {(ov['total'] or 0)/1e8:.3f} 亿元 |",
        f"| 中位单价 | {wan(ov['median'])} 万元 |",
        f"| 平均单价 | {wan(ov['mean'])} 万元 |",
        f"| 四分位区间 | {wan(ov['p25'])}–{wan(ov['p75'])} 万元 |",
        "", "## 热点标的（按成交额）", "",
        "| 主题 | 项目数 | 成交金额(万元) | 中位单价(万元) |", "|---|---|---|---|",
    ]
    for t in stats["topics"][:8]:
        lines.append(f"| {t['name']} | {t['count']} | {t['amount']/1e4:,.0f} | {wan(t['median'])} |")

    lines += ["", "## 金额分布", "", "| 价格带 | 项目数 | 占比 |", "|---|---|---|"]
    for d in stats["amount_dist"]:
        lines.append(f"| {d['label']} | {d['count']} | {d['share']}% |")

    lines += ["", "## 头部中标方", "", "| 中标单位 | 次数 | 金额(万元) | 份额 |",
              "|---|---|---|---|"]
    for s in stats["suppliers"][:10]:
        lines.append(f"| {s['name']} | {s['count']} | {s['amount']/1e4:,.0f} | {s['share']}% |")

    lines += ["", "## 高价值项目 Top10", "", "| 项目 | 中标单位 | 金额(万元) | 地区 |",
              "|---|---|---|---|"]
    for d in stats.get("top_deals", [])[:10]:
        lines.append(f"| {d['name'][:40]} | {d['winner'][:22]} | {d['amount']/1e4:,.0f} | {d['region']} |")

    lines += ["", "---", "",
              "> 口径：宽口径覆盖 ESG 核心服务、双碳与碳管理、绿色低碳周边；"
              "已剔除楼宇工程与设备采购等误命中，并按采购人+项目名聚合多公告。",
              "> 统计区间首尾年份未必是完整自然年，不做年度同比；跨年趋势以月度走势为准。",
              "> 数据仅来自政府采购公开渠道，不含国企与央企自主采购平台，绝对金额应视为下限。"]
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def main():
    ap = argparse.ArgumentParser(description="渲染 ESG 投标情报报告")
    ap.add_argument("--data", default="./esg_bid_data")
    ap.add_argument("--out", default=None)
    ap.add_argument("--title", default="ESG / 双碳 / 可持续发展 招投标情报分析报告")
    ap.add_argument("--period", default="")
    ap.add_argument("--narrative", default=None, help="外部洞察稿 (Markdown)")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    outdir = Path(args.out) if args.out else data / "report"
    outdir.mkdir(parents=True, exist_ok=True)

    stats = json.loads((data / "stats.json").read_text(encoding="utf-8"))
    projects = json.loads((data / "projects.json").read_text(encoding="utf-8"))

    period = args.period
    if not period:
        dates = [p["first_date"] or p["award_date"] for p in projects if p.get("first_date") or p.get("award_date")]
        period = f"{min(dates)} ~ {max(dates)}" if dates else datetime.now().strftime("%Y-%m")
    stats["meta"]["period"] = period

    if args.narrative:
        narrative_html = md2html(Path(args.narrative).read_text(encoding="utf-8"))
    else:
        narrative_html = build_narrative(stats, projects)

    h = render_html(stats, projects, outdir / "esg_bid_report.html",
                    args.title, period, narrative_html)
    x = render_excel(projects, stats, outdir / "esg_bid_ledger.xlsx")
    m = render_markdown(stats, outdir / "esg_bid_brief.md", args.title, period)

    print(f"HTML  -> {h}")
    if x:
        print(f"Excel -> {x}")
    print(f"MD    -> {m}")


if __name__ == "__main__":
    main()
