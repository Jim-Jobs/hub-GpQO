#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
每日招标监控简报

职责: 抓当日新增 -> 过滤相关性 -> 与已推送记录去重 -> 产出 Markdown + HTML 简报。
去重状态存 briefs/pushed.json, 同日多次运行不会重复推送。

用法:
  python daily.py --data ./esg_bid_data                     # 抓昨日至今并出简报
  python daily.py --data ./esg_bid_data --days 3            # 回溯 3 天(补漏)
  python daily.py --data ./esg_bid_data --no-crawl --raw xxx.json   # 只渲染已有数据
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from amount_parser import extract as extract_amount  # noqa: E402
from normalize import classify, detect_nature, infer_industry, load_taxonomy  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SCRIPTS = Path(__file__).parent
DEADLINE_RE = re.compile(
    r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*"
    r"(?:（[^）]{0,6}）)?\s*(\d{1,2})\s*[时:：]\s*(\d{1,2})?\s*分?\s*"
    r"(?:（北京时间）|\(北京时间\))?\s*前?"
)


def find_deadline(text: str) -> str:
    """从招标公告正文提取投标截止/开标时间。取正文中首个符合"年月日+时分"的时间。"""
    if not text:
        return ""
    for m in DEADLINE_RE.finditer(text[:4000]):
        y, mo, d, hh, mm = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(hh), int(mm or 0))
        except ValueError:
            continue
        # 上下文需与"提交/截止/开标"相关, 避免抓到服务期等无关日期
        ctx = text[max(0, m.start() - 60): m.end() + 30]
        if re.search(r"(提交|截止|开标|响应文件|投标文件|递交)", ctx):
            return dt.strftime("%Y-%m-%d %H:%M")
    return ""


def run_crawler(data_dir: Path, days: int, keywords_file: Path | None) -> Path | None:
    cmd = [sys.executable, str(SCRIPTS / "ccgp_crawler.py"),
           "--mode", "daily", "--days", str(days), "--out", str(data_dir),
           "--detail-types", "招标,采购,磋商,谈判,询价,单一来源,中标,成交,结果",
           "--min-delay", "2.0", "--max-delay", "3.0"]
    if keywords_file and keywords_file.exists():
        cmd += ["--keywords-file", str(keywords_file)]
    print("[1/3] 抓取当日公告 ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
        return None
    files = sorted((data_dir / "raw").glob("notices_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def load_pushed(p: Path) -> set:
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")).get("ids", []))
    except Exception:
        return set()


def save_pushed(p: Path, ids: set):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"ids": sorted(ids), "last_run": datetime.now().isoformat()},
                            ensure_ascii=False), encoding="utf-8")


def clip(s: str, n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n].rstrip() + "…"


def md_brief(rows: list[dict], today: str, days: int, stats: dict) -> str:
    L = [f"# ESG / 双碳 招标日报 · {today}", ""]
    L.append(f"> 扫描区间：近 {days} 天　|　新增 {len(rows)} 条相关公告　|　"
             f"其中招标中 {stats['tender']} 条、已成交 {stats['awarded']} 条、其他 {stats['other']} 条")
    L.append("")

    tender = [r for r in rows if r["status"] == "tender"]
    awarded = [r for r in rows if r["status"] == "awarded"]

    if tender:
        L += ["## 一、进行中的招标（可跟进）", ""]
        L += ["| 项目名称 | 采购人 | 地区 | 标的主题 | 发布时间 | 截止/开标 |",
              "|---|---|---|---|---|---|"]
        for r in tender[:40]:
            L.append(f"| {clip(r['title'], 46)} | {clip(r['buyer'], 18)} | {r['region']} | "
                     f"{r['topic']} | {r['pub_date']} | {r['deadline'] or '—'} |")
        L.append("")

    if awarded:
        L += ["## 二、中标结果（竞争情报）", ""]
        L += ["| 项目名称 | 中标单位 | 金额(万元) | 采购人 | 地区 |",
              "|---|---|---|---|---|"]
        for r in awarded[:40]:
            amt = f"{r['amount_yuan']/1e4:,.1f}" if r["amount_yuan"] else "—"
            L.append(f"| {clip(r['title'], 42)} | {clip(r['supplier'] or '—', 22)} | {amt} | "
                     f"{clip(r['buyer'], 16)} | {r['region']} |")
        L.append("")

    obs = stats["observations"]
    if obs:
        L += ["## 三、今日观察", ""]
        L += [f"- {o}" for o in obs]
        L.append("")

    obs = stats["observations"]
    if obs:
        L += ["## 三、今日观察", ""]
        L += [f"- {o}" for o in obs]
        L.append("")

    L += ["---", "",
          "> 数据来源：中国政府采购网。已按宽口径（ESG核心 / 双碳 / 绿色周边）筛选，"
          "并剔除楼宇工程、设备采购等关键词误命中。"]
    return "\n".join(L)


HTML_HEAD = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title><style>
:root{--ink:#0f172a;--ink2:#334155;--muted:#64748b;--line:#e2e8f0;--blue:#2563eb;
--green:#059669;--amber:#d97706}
*{box-sizing:border-box}body{margin:0;background:#f6f8fb;color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
font-size:14px;line-height:1.65}.wrap{max-width:1080px;margin:0 auto;padding:36px 24px 60px}
.eyebrow{font-size:11.5px;letter-spacing:.16em;text-transform:uppercase;color:var(--blue);font-weight:600}
h1{font-size:26px;margin:8px 0 10px;font-weight:700}.sub{color:var(--muted);font-size:13px}
.pill{display:inline-block;padding:2px 9px;border-radius:20px;font-size:12px;font-weight:600;margin-right:6px}
.p1{background:#eff6ff;color:#1d4ed8}.p2{background:#ecfdf5;color:#047857}.p3{background:#f1f5f9;color:#475569}
section{margin-top:30px}.sec{display:flex;align-items:baseline;gap:10px;margin-bottom:12px}
.sec h2{font-size:17px;margin:0;font-weight:700}.cnt{font-size:12px;color:var(--muted)}
.card{background:#fff;border:1px solid var(--line);border-radius:12px;overflow:hidden;
box-shadow:0 1px 3px rgba(15,23,42,.05)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;font-weight:600;color:var(--muted);font-size:11.5px;letter-spacing:.03em;
padding:11px 14px;background:#fafbfc;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:11px 14px;border-bottom:1px solid #f1f5f9;vertical-align:top}
tr:last-child td{border-bottom:none}tr:hover td{background:#fafbfc}
td.n{text-align:right;font-variant-numeric:tabular-nums;font-weight:600;white-space:nowrap}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:5px;background:#f1f5f9;color:var(--ink2);white-space:nowrap}
td.nw{white-space:nowrap;color:#64748b}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.warn{color:var(--amber);font-weight:600}
.obs{background:#fff;border:1px solid var(--line);border-radius:12px;padding:8px 22px 16px}
.obs li{margin:10px 0;color:var(--ink2)}.obs b{color:var(--ink)}
footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);color:var(--muted);font-size:11.5px}
</style></head><body><div class="wrap">"""


def html_brief(rows: list[dict], today: str, days: int, stats: dict) -> str:
    tender = [r for r in rows if r["status"] == "tender"]
    awarded = [r for r in rows if r["status"] == "awarded"]

    h = [HTML_HEAD.replace("__TITLE__", f"ESG 招标日报 {today}")]
    h.append('<div class="eyebrow">Daily Bid Monitor</div>')
    h.append(f"<h1>ESG / 双碳 招标日报 · {today}</h1>")
    h.append(f'<div class="sub"><span class="pill p1">招标中 {stats["tender"]}</span>'
             f'<span class="pill p2">已成交 {stats["awarded"]}</span>'
             f'<span class="pill p3">其他 {stats["other"]}</span>'
             f'　扫描近 {days} 天 · 新增相关公告 {len(rows)} 条 · '
             f'生成于 {datetime.now():%Y-%m-%d %H:%M}</div>')

    if tender:
        h.append(f'<section><div class="sec"><h2>一、进行中的招标</h2>'
                 f'<span class="cnt">{len(tender)} 条可跟进</span></div><div class="card"><table>')
        h.append("<thead><tr><th>项目名称</th><th>采购人</th><th>标的主题</th>"
                 "<th>地区</th><th>发布</th><th>截止/开标</th></tr></thead><tbody>")
        for r in tender[:50]:
            dl = (f'<span class="warn">{r["deadline"]}</span>' if r["deadline"]
                  else '<span style="color:#94a3b8">—</span>')
            h.append(f'<tr><td><a href="{r["detail_url"]}" target="_blank">{clip(r["title"],52)}</a></td>'
                     f'<td style="color:#475569">{clip(r["buyer"],20)}</td>'
                     f'<td><span class="tag">{r["topic"]}</span></td>'
                     f'<td class="nw">{r["region"]}</td>'
                     f'<td class="nw">{r["pub_date"]}</td><td class="nw">{dl}</td></tr>')
        h.append("</tbody></table></div></section>")

    if awarded:
        h.append(f'<section><div class="sec"><h2>二、中标结果</h2>'
                 f'<span class="cnt">竞争情报 {len(awarded)} 条</span></div><div class="card"><table>')
        h.append("<thead><tr><th>项目名称</th><th>中标单位</th><th>金额(万元)</th>"
                 "<th>采购人</th><th>地区</th></tr></thead><tbody>")
        for r in awarded[:50]:
            amt = f"{r['amount_yuan']/1e4:,.1f}" if r["amount_yuan"] else "—"
            h.append(f'<tr><td><a href="{r["detail_url"]}" target="_blank">{clip(r["title"],48)}</a></td>'
                     f'<td style="color:#334155;font-weight:600">{clip(r["supplier"] or "—",24)}</td>'
                     f'<td class="n">{amt}</td>'
                     f'<td style="color:#64748b">{clip(r["buyer"],18)}</td>'
                     f'<td style="color:#64748b">{r["region"]}</td></tr>')
        h.append("</tbody></table></div></section>")

    if stats["observations"]:
        h.append('<section><div class="sec"><h2>三、今日观察</h2></div><div class="obs"><ul>')
        h += [f"<li>{re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', o)}</li>"
              for o in stats["observations"]]
        h.append("</ul></div></section>")

    h.append("<footer>数据来源：中国政府采购网 · 宽口径（ESG 核心 / 双碳 / 绿色周边）"
             "· 已剔除楼宇工程与设备采购等误命中</footer></div></body></html>")
    return "".join(h)


def observations(rows: list[dict]) -> list[str]:
    """规则化今日观察: 只在数据支持时才输出, 不写空话。"""
    obs = []
    tender = [r for r in rows if r["status"] == "tender"]
    awarded = [r for r in rows if r["status"] == "awarded"]

    from collections import Counter
    tc = Counter(r["topic"] for r in rows)
    if tc:
        t, n = tc.most_common(1)[0]
        if n >= 3:
            obs.append(f"**{t}** 类标的今日集中出现 **{n}** 条，是当前最密集的需求方向。")

    rc = Counter(r["region"] for r in rows if r["region"])
    if rc and rc.most_common(1)[0][1] >= 3:
        r, n = rc.most_common(1)[0]
        obs.append(f"地域上以 **{r}** 最活跃（{n} 条），建议优先核对本地代理机构与评分办法。")

    urgent = [r for r in tender if r.get("deadline")]
    near = []
    for r in urgent:
        try:
            dd = datetime.strptime(r["deadline"], "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        left = (dd - datetime.now()).days
        if 0 <= left <= 7:
            near.append((r, left))
    if near:
        near.sort(key=lambda x: x[1])
        obs.append(f"有 **{len(near)}** 个招标项目在 7 天内截止，"
                   f"其中最近的「{clip(near[0][0]["title"],28)}」剩余 "
                   f"**{near[0][1]}** 天，需立即评估是否响应。")

    big = [r for r in awarded if r["amount_yuan"] and r["amount_yuan"] >= 3_000_000]
    if big:
        b = max(big, key=lambda x: x["amount_yuan"])
        obs.append(f"今日最大成交为「{clip(b["title"],26)}」，金额 **{b['amount_yuan']/1e4:,.0f} 万元**，"
                   f"中标方 {b['supplier'] or '未知'}，可作为同类项目的规模参照。")

    newsup = [r for r in awarded if r["supplier"]]
    if len(newsup) >= 3:
        from collections import Counter as C2
        sc = C2(r["supplier"] for r in newsup)
        if sc.most_common(1)[0][1] >= 2:
            s, n = sc.most_common(1)[0]
            obs.append(f"**{s}** 今日拿下 {n} 个项目，需关注其报价策略与区域布局。")

    if not obs:
        obs.append("今日无新增相关公告。" if not rows
                   else "今日无显著异常，新增公告以常规采购为主。")
    return obs


def main():
    ap = argparse.ArgumentParser(description="每日招标简报")
    ap.add_argument("--data", default="./esg_bid_data")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--no-crawl", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="忽略已推送记录, 重新生成当日简报(补发/调试用)")
    ap.add_argument("--raw", default=None, help="直接指定已有 raw json")
    args = ap.parse_args()

    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    tax = load_taxonomy()

    raw_path = Path(args.raw) if args.raw else None
    if raw_path is None and not args.no_crawl:
        raw_path = run_crawler(data, args.days, SCRIPTS.parent / "references" / "taxonomy.json")
    if raw_path is None:
        cands = sorted((data / "raw").glob("notices_*.json"), key=lambda p: p.stat().st_mtime)
        if not cands:
            print("没有可用的抓取结果, 请先运行 ccgp_crawler.py")
            return
        raw_path = cands[-1]

    print(f"[2/3] 处理 {raw_path.name} ...", flush=True)
    records = json.loads(raw_path.read_text(encoding="utf-8"))

    pushed_path = data / "briefs" / "pushed.json"
    pushed = set() if args.force else load_pushed(pushed_path)

    cutoff = (date.today() - timedelta(days=args.days)).isoformat()
    rows, noise = [], tax["noise_patterns"]
    for n in records:
        title = n.get("title", "")
        if not title or any(re.search(p, title) for p in noise):
            continue
        if n.get("id") in pushed:
            continue
        pub = n.get("pub_date", "")
        if pub and pub < cutoff:
            continue

        tier, tiers, relevance = classify(title, tax)
        if not tier or relevance == "low":
            continue
        nature = detect_nature(title)
        if nature != "service":
            continue

        ntype = n.get("notice_type", "")
        status = "other"
        for k, v in tax["status_map"].items():
            if k in ntype:
                status = v
                break

        info = extract_amount(n.get("detail_text", "")) if n.get("detail_text") else {
            "amount_yuan": None, "supplier": ""}
        rows.append({
            "id": n["id"], "title": title, "buyer": n.get("buyer", ""),
            "region": n.get("region", ""), "pub_date": pub,
            "notice_type": ntype, "detail_url": n.get("detail_url", ""),
            "status": status, "tier": tier, "relevance": relevance,
            "industry": infer_industry(n.get("buyer", ""), tax),
            "topic": "", "amount_yuan": info.get("amount_yuan"),
            "supplier": info.get("supplier", ""),
            "deadline": find_deadline(n.get("detail_text", "")),
        })

    # 主题归属 (与 analyze.py 保持一致的优先级)
    from analyze import TOPIC_RULES, TOPIC_FALLBACK
    for r in rows:
        r["topic"] = next((lb for lb, ws in TOPIC_RULES
                           if any(w.lower() in r["title"].lower() for w in ws)), TOPIC_FALLBACK)

    # 先按日期降序, 再按"是否招标中"稳定排序, 保证两组内部都是最新在前
    rows.sort(key=lambda r: r["pub_date"], reverse=True)
    rows.sort(key=lambda r: 0 if r["status"] == "tender" else 1)

    today = date.today().isoformat()
    st = {
        "tender": sum(1 for r in rows if r["status"] == "tender"),
        "awarded": sum(1 for r in rows if r["status"] == "awarded"),
        "other": sum(1 for r in rows if r["status"] not in ("tender", "awarded")),
        "observations": observations(rows),
    }

    brief_dir = data / "briefs"
    brief_dir.mkdir(parents=True, exist_ok=True)
    md_path = brief_dir / f"{today}.md"
    html_path = brief_dir / f"{today}.html"
    md_path.write_text(md_brief(rows, today, args.days, st), encoding="utf-8")
    html_path.write_text(html_brief(rows, today, args.days, st), encoding="utf-8")

    save_pushed(pushed_path, pushed | {r["id"] for r in rows})

    print(f"[3/3] 新增 {len(rows)} 条 | 招标 {st['tender']} / 成交 {st['awarded']} / 其他 {st['other']}")
    print(f"  -> {md_path}")
    print(f"  -> {html_path}")


if __name__ == "__main__":
    main()
