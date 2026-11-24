"""主入口：检索 → 筛选 → 总结 → 成文 → 质检（README §8）。"""

from __future__ import annotations

import argparse
import html as html_lib
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import yaml
from pydantic import BaseModel, ConfigDict

from scripts import contracts, rank_filter, render_report, search, summarize

CONFIG_DIR = ROOT / "config"
OUTPUT_DIR = ROOT / "output"
SCHEMA_VERSION = contracts.SCHEMA_VERSION
REQUIRED_ENV = ("SEARCH_API_KEY", "SEARCH_API_PROVIDER", "LLM_API_KEY", "LLM_MODEL")
GROUP_TO_CATEGORY = {
    "policy": "policy",
    "standard": "policy",
    "industry": "industry",
    "client": "company",
    "peer": "peer",
}
CLIENT_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
MONTH_RE = re.compile(r"(?:19|20)\d{2}-(?:0[1-9]|1[0-2])")


class ReportPolicy(BaseModel):
    model_config = ConfigDict(frozen=True)

    word_min: int = 4500
    word_max: int = 5500
    table_min: int = 3
    table_max: int = 4

    @classmethod
    def from_profile(cls, profile: dict) -> ReportPolicy:
        length = int(profile.get("report_length") or 5000)
        parts = str(profile.get("table_count") or "3-4").split("-", 1)
        table_min = int(parts[0])
        table_max = int(parts[1]) if len(parts) > 1 else table_min
        return cls(
            word_min=length * 9 // 10,
            word_max=length * 11 // 10,
            table_min=table_min,
            table_max=table_max,
        )


def load_env(path: Path) -> dict[str, str]:
    """.env 与进程环境合并，进程环境优先。"""
    values = dict(os.environ)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            values.setdefault(key.strip(), raw.strip())
    return values


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _validated_out_dir(parser: argparse.ArgumentParser, client: str, month: str) -> Path:
    """客户端与月份都是路径片段，必须先白名单化并确认输出仍在 output/ 内。"""
    if not CLIENT_RE.fullmatch(client):
        parser.error("--client 仅允许小写字母、数字、-、_（≤ 32 字符）")
    if not MONTH_RE.fullmatch(month):
        parser.error("--month 必须为 YYYY-MM")
    out_dir = (OUTPUT_DIR / client / month).resolve()
    if not out_dir.is_relative_to(OUTPUT_DIR.resolve()):
        parser.error("输出路径越界")
    return out_dir


def build_profile(client_name: str, month: str) -> dict:
    profile = load_yaml(CONFIG_DIR / f"client_{client_name}.yaml")
    sources = load_yaml(CONFIG_DIR / "sources.yaml")
    profile["month"] = month
    profile["whitelist"] = sources.get("whitelist", [])
    profile["queries"] = sources.get("queries", {})
    return profile


def expand_queries(templates: dict, profile: dict) -> list[dict]:
    month = profile.get("month", "")
    client = profile.get("client", "")
    peers = list((profile.get("peers") or {}).get("domestic", []))
    queries: list[dict] = []
    for group, entries in templates.items():
        category = GROUP_TO_CATEGORY.get(group, group)
        for template in entries or []:
            queries.append(
                {
                    "category": category,
                    "query": template.format(
                        month=month, client=client, peer=(peers[0] if peers else "")
                    ),
                }
            )
    for peer in peers:
        queries.append({"category": "peer", "query": f"{month} {peer} ESG"})
    return queries


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def report_stats(markup: str) -> dict:
    """统计渲染稿字数（CJK 字符 + 拉丁词）与表格数量（README §14）。"""
    text = html_lib.unescape(re.sub(r"<[^>]+>", " ", markup))
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z0-9]+", text))
    return {
        "word_count": cjk_chars + latin_words,
        "cjk_chars": cjk_chars,
        "latin_words": latin_words,
        "tables": len(re.findall(r"<table\b", markup, flags=re.IGNORECASE)),
    }


def build_checklist(
    summarized: list[dict],
    gaps: list[dict],
    undated: int,
    month: str,
    stats: dict,
    *,
    policy: ReportPolicy | None = None,
    red_lines: list[str] | None = None,
) -> str:
    policy = policy or ReportPolicy()
    red_line_text = "、".join(red_lines or ["不预测政策", "不评论短期煤价"])
    total = len(summarized)
    traceable = sum(1 for item in summarized if item.get("url"))
    rate = round(traceable / total * 100, 1) if total else 0.0
    todo = [
        item for item in summarized if item.get("todo") or item.get("impact_level") == "待核实"
    ]
    lines = [
        f"# 人工复核清单 · {month}",
        "",
        f"- 来源可追溯率：{rate}%（{traceable}/{total}）",
        f"- 待核实 / 待总结项：{len(todo)} 条",
        f"- 检索缺口（query 失败）：{len(gaps)} 个",
        f"- 无日期未归属条目：{undated} 条",
        f"- 字数：{stats['word_count']} 字（目标 {policy.word_min}–{policy.word_max}）",
        f"- 表格数量：{stats['tables']} 张（目标 {policy.table_min}–{policy.table_max}）",
        "",
        "## 复核要点",
        "",
        "- [ ] 抽查 5 条事实性表述，确认链接可访问且内容一致",
        f"- [ ] 核对影响等级与建议是否符合「{red_line_text}」红线",
        "- [ ] 字数 / 表格数量偏离目标时确认是否需要调整篇幅",
        "- [ ] 处理下列待核实项",
        "",
    ]
    for index, item in enumerate(todo, start=1):
        lines.append(
            f"{index}. {item.get('title', '')} — {item.get('impact_one_liner', '')}"
            f"（{item.get('source', '')}）"
        )
    if gaps:
        lines.extend(["", "## 检索缺口", ""])
        for gap in gaps:
            lines.append(f"- {gap['query']}：{gap['reason']}")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="ESG 月报生成流水线")
    parser.add_argument("--client", required=True, help="客户 slug，如 shenhua")
    parser.add_argument("--month", required=True, help="目标月份，如 2026-10")
    parser.add_argument("--format", default="html", help="html 或 html,docx")
    parser.add_argument("--topn", type=int, default=5, help="每类保留条数")
    parser.add_argument("--threshold", type=float, default=0.4, help="三维总分阈值")
    args = parser.parse_args(argv)
    out_dir = _validated_out_dir(parser, args.client, args.month)

    env = load_env(CONFIG_DIR / ".env")
    missing = [key for key in REQUIRED_ENV if not env.get(key)]
    if missing:
        print(
            f"缺少环境变量：{', '.join(missing)}；请填写 config/.env（模板见 config/.env.example）",
            file=sys.stderr,
        )
        return 2

    profile = build_profile(args.client, args.month)
    policy = ReportPolicy.from_profile(profile)
    run_date = date.today()
    out_dir.mkdir(parents=True, exist_ok=True)

    client = search.SearchClient(env["SEARCH_API_PROVIDER"], env["SEARCH_API_KEY"])
    queries = expand_queries(profile["queries"], profile)
    raw, gaps = search.fetch_all(client, queries)
    raw = search.dedupe(raw)

    undated = sum(1 for item in raw if search.parse_date(item.get("published_at")) is None)
    filtered = [item for item in raw if search.event_in_window(item.get("published_at"), args.month)]

    stamp = datetime.now().isoformat(timespec="seconds")
    start, end = search.window_bounds(args.month)
    raw_artifact = contracts.RawArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=stamp,
        api=env["SEARCH_API_PROVIDER"],
        window=contracts.Window(start=start.isoformat(), end=end.isoformat()),
        gaps=[contracts.Gap(**gap) for gap in gaps],
        results=[contracts.RawResult.from_mapping(item) for item in raw],
    )
    write_json(out_dir / "raw.json", contracts.as_dict(raw_artifact))

    ranked = rank_filter.rank(
        filtered, profile, topn=args.topn, threshold=args.threshold, now=run_date
    )
    ranked_artifact = contracts.RankedArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=stamp,
        threshold=args.threshold,
        topn=args.topn,
        groups={
            category: [contracts.RankedResult.from_mapping(item) for item in rows]
            for category, rows in ranked.items()
        },
    )
    write_json(out_dir / "ranked.json", contracts.as_dict(ranked_artifact))

    ordered = [
        item
        for key in ("policy", "industry", "company", "peer", "other")
        for item in ranked.get(key, [])
    ]
    summarized = summarize.summarize(ordered, profile, env)
    summarized_artifact = contracts.SummarizedArtifact(
        schema_version=SCHEMA_VERSION,
        generated_at=stamp,
        model=env["LLM_MODEL"],
        items=[contracts.SummarizedItem.from_mapping(item) for item in summarized],
    )
    write_json(out_dir / "summarized.json", contracts.as_dict(summarized_artifact))

    formats = {fmt.strip().lower() for fmt in args.format.split(",") if fmt.strip()}
    html_path = out_dir / "report.html"
    html_content = render_report.render(summarized, profile, now=run_date, gaps=gaps)
    html_path.write_text(html_content, encoding="utf-8")
    written = [html_path]
    if "docx" in formats:
        written.append(
            render_report.render_docx(
                summarized, profile, out_dir / "report.docx", now=run_date, gaps=gaps
            )
        )

    checklist = out_dir / "review_checklist.md"
    checklist.write_text(
        build_checklist(
            summarized,
            gaps,
            undated,
            args.month,
            report_stats(html_content),
            policy=policy,
            red_lines=profile.get("red_lines"),
        ),
        encoding="utf-8",
    )

    for path in [*written, checklist]:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
