"""渲染层：Jinja2 → HTML；python-docx → Word（README §9、§12.2）。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "templates"

SECTIONS = [
    ("policy", "一、ESG 政策 / 评级 / 标准动态"),
    ("industry", "二、行业动态与最佳实践"),
    ("company", "三、客户公司动态与 ESG 影响"),
    ("peer", "四、对标企业关键 ESG 行动"),
]
SNIPPET_LIMIT = 150


def _excerpt(text: str, limit: int = SNIPPET_LIMIT) -> str:
    """来源摘要截断，避免整段转载内容撑爆篇幅。"""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _date_label(value) -> str:
    """展示用日期：ISO 日期时间截断为 YYYY-MM-DD，空值返回空串由模板兜底。"""
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 and text[4] == "-" and text[7] == "-" else text


def _safe_url(value) -> str:
    """来源链接只放行 http/https，避免搜索结果里的 javascript: 等协议进入报告。"""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""
    return raw


def _display_rows(items: list[dict]) -> list[dict]:
    return [
        dict(
            item,
            url=_safe_url(item.get("url")),
            snippet_excerpt=_excerpt(item.get("snippet", "")),
            date=_date_label(item.get("date") or item.get("published_at")),
        )
        for item in items
    ]


def _section_stats(items: list[dict]) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for key, _ in SECTIONS:
        rows = [item for item in items if (item.get("section") or item.get("category")) == key]
        levels = {"高": 0, "中": 0, "低": 0, "待核实": 0}
        for row in rows:
            level = row.get("impact_level") or "待核实"
            levels[level] = levels.get(level, 0) + 1
        dates = sorted(
            value
            for value in (row.get("date") or row.get("published_at") for row in rows)
            if value
        )
        sources: list[str] = []
        for row in rows:
            source = row.get("source") or ""
            if source and source not in sources:
                sources.append(source)
        stats[key] = {
            "count": len(rows),
            "levels": levels,
            "sources": sources[:3],
            "date_start": dates[0] if dates else "",
            "date_end": dates[-1] if dates else "",
            "highlights": [row for row in rows if row.get("impact_level") == "高"][:3],
        }
    return stats


def _high_items(items: list[dict], limit: int = 8) -> list[dict]:
    return [item for item in items if item.get("impact_level") == "高"][:limit]


def _group(items: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    groups: dict[str, list[dict]] = {key: [] for key, _ in SECTIONS}
    extras: list[dict] = []
    for item in items:
        section = item.get("section") or item.get("category") or ""
        if section in groups:
            groups[section].append(item)
        else:
            extras.append(item)
    return groups, extras


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )


def render(
    items: list[dict],
    profile: dict,
    now: date | None = None,
    gaps: list[dict] | None = None,
) -> str:
    now = now or date.today()
    display = _display_rows(items)
    groups, extras = _group(display)
    todo_items = [
        item for item in items if item.get("todo") or item.get("impact_level") == "待核实"
    ]
    sources: list[dict] = []
    seen: set[str] = set()
    for item in items:
        url = _safe_url(item.get("url"))
        if url and url not in seen:
            seen.add(url)
            sources.append({"source": item.get("source", ""), "url": url})
    template = _environment().get_template("monthly_report.html.j2")
    return template.render(
        items=display,
        groups=groups,
        extras=extras,
        todo_items=todo_items,
        sources=sources,
        profile=profile,
        now=now,
        sections=SECTIONS,
        section_stats=_section_stats(display),
        high_items=_high_items(items),
        gaps=gaps or [],
    )


def render_docx(
    items: list[dict], profile: dict, out_path, now: date | None = None, gaps: list[dict] | None = None
) -> Path:
    from docx import Document

    now = now or date.today()
    display = _display_rows(items)
    groups, _ = _group(display)
    stats = _section_stats(display)
    document = Document()
    document.add_heading(f"ESG 月报 · {profile.get('client', '')}", level=0)
    meta = f"生成时间：{now.isoformat()}"
    if profile.get("month"):
        meta = f"报告月份：{profile['month']} ｜ " + meta
    document.add_paragraph(meta)
    document.add_heading("阅读说明", level=1)
    document.add_paragraph(
        "本报告按自然月窗口归纳公开信息，每条均附来源与日期；同一事件以最早来源为准，"
        "无法访问或未经核实的内容单列为「待核实」，不做推测。"
        "影响等级口径：高为直接影响评级、合规或重大经营；中为行业趋势或同业动作；低为一般舆情。"
    )

    for key, title in SECTIONS:
        document.add_heading(title, level=1)
        stat = stats[key]
        document.add_paragraph(
            f"本节共收录 {stat['count']} 条：高 {stat['levels']['高']} 条、"
            f"中 {stat['levels']['中']} 条、低 {stat['levels']['低']} 条；"
            f"覆盖来源：{'、'.join(stat['sources']) or 'N/A'}；"
            f"日期范围：{stat['date_start'] or 'N/A'} 至 {stat['date_end'] or 'N/A'}。"
        )
        rows = groups[key]
        if not rows:
            document.add_paragraph("本期无收录信息。")
            continue
        table = document.add_table(rows=1, cols=4)
        table.style = "Table Grid"
        for cell, text in zip(table.rows[0].cells, ("来源", "日期", "信息", "影响等级")):
            cell.text = text
        for item in rows:
            cells = table.add_row().cells
            cells[0].text = str(item.get("source", ""))
            cells[1].text = str(item.get("date") or item.get("published_at") or "N/A")
            cells[2].text = (
                f"{item.get('title', '')}\n{item.get('impact_one_liner', '')}"
                f"\n{item.get('snippet_excerpt', '')}"
            ).strip()
            cells[3].text = str(item.get("impact_level", ""))

    document.add_heading("五、下月关注与信息缺口", level=1)
    todo = [item for item in items if item.get("todo") or item.get("impact_level") == "待核实"]
    if todo:
        document.add_paragraph("以下信息需人工复核：")
        for item in todo:
            document.add_paragraph(
                f"{item.get('title', '')}（{item.get('source', '')}）— "
                f"{item.get('impact_one_liner', '')}",
                style="List Bullet",
            )
    else:
        document.add_paragraph("本期无待核实项。")
    if gaps:
        document.add_paragraph("检索缺口：")
        for gap in gaps:
            document.add_paragraph(f"{gap['query']}：{gap['reason']}", style="List Bullet")

    destination = Path(out_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(destination))
    return destination


if __name__ == "__main__":
    demo_profile = {"client": "中国神华", "industry": "煤炭开采与煤电一体化", "month": "2026-10"}
    demo_items: list[dict] = [
        {
            "title": "国家能源局发布煤炭产能储备管理办法",
            "source": "国家能源局",
            "url": "https://www.nea.gov.cn/art/2026/10/8/a.html",
            "date": "2026-10-08",
            "impact_level": "高",
            "impact_one_liner": "提高产能储备与信息披露合规要求。",
            "category": "policy",
        },
        {
            "title": "某来源无法访问的待核实信息",
            "source": "演示来源",
            "url": "",
            "date": "2026-10-15",
            "impact_level": "待核实",
            "impact_one_liner": "待总结（演示数据）",
            "todo": True,
            "category": "company",
        },
    ]
    out_dir = ROOT / "output" / "_demo"
    html_path = out_dir / "report.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(render(demo_items, demo_profile, now=date(2026, 11, 3)), encoding="utf-8")
    docx_path = render_docx(demo_items, demo_profile, out_dir / "report.docx", now=date(2026, 11, 3))
    print(html_path)
    print(docx_path)
