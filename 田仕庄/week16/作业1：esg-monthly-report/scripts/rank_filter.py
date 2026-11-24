"""筛选层：三维打分、阈值过滤、每类 Top-N（README §8、§12.1）。"""

from __future__ import annotations

import re
import sys
import urllib.parse
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.search import normalize_url, parse_date

WEIGHTS = {"relevance": 0.5, "authority": 0.3, "timeliness": 0.2}
WHITELIST_AUTHORITY = 1.0
DEFAULT_AUTHORITY = 0.3
TIMELINESS_WINDOW_DAYS = 60
QUERY_HIT_WEIGHT = 0.25
_QUERY_SPLIT = re.compile(r"[\s/|，。；：、（）()【】\[\]·—]+")


def _query_terms(query: str) -> list[str]:
    terms = []
    for token in _QUERY_SPLIT.split(query or ""):
        token = token.strip()
        if len(token) >= 2 and not token.isdigit():
            terms.append(token)
    return terms


def _relevance(item: dict, keywords: list[str]) -> float:
    """相关性 = 客户关键词命中（各 0.5）+ 检索词命中加成（各 0.25），上限 1.0。"""
    text = f"{item.get('title', '')} {item.get('snippet', '')}"
    keyword_hits = sum(1 for keyword in keywords if keyword and keyword in text)
    query_hits = sum(1 for term in _query_terms(item.get("query", "")) if term in text)
    return min(1.0, keyword_hits / 2.0 + QUERY_HIT_WEIGHT * query_hits)


def _authority(item: dict, whitelist: list[str]) -> float:
    host = urllib.parse.urlsplit(item.get("url", "")).netloc.lower()
    for domain in whitelist:
        domain = (domain or "").lower().strip()
        if domain and (host == domain or host.endswith("." + domain)):
            return WHITELIST_AUTHORITY
    return DEFAULT_AUTHORITY


def _timeliness(item: dict, now: date) -> float:
    published = parse_date(item.get("published_at") or item.get("date"))
    if published is None:
        return 0.0
    age_days = (now - published).days
    return max(0.0, min(1.0, 1.0 - age_days / TIMELINESS_WINDOW_DAYS))


def score(item: dict, profile: dict, now: date | None = None) -> dict:
    now = now or date.today()
    parts = {
        "relevance": _relevance(item, profile.get("keywords", [])),
        "authority": _authority(item, profile.get("whitelist", [])),
        "timeliness": _timeliness(item, now),
    }
    parts["total"] = round(sum(parts[name] * WEIGHTS[name] for name in WEIGHTS), 4)
    return parts


def rank(
    results: list[dict],
    profile: dict,
    topn: int = 5,
    threshold: float = 0.4,
    now: date | None = None,
) -> dict[str, list[dict]]:
    now = now or date.today()
    grouped: dict[str, list[dict]] = {}
    for item in results:
        item_score = score(item, profile, now)
        if item_score["total"] < threshold:
            continue
        enriched = dict(item)
        enriched["score"] = item_score
        enriched["dedup_key"] = normalize_url(item.get("url", "")) or item.get("title", "")
        grouped.setdefault(item.get("category") or "other", []).append(enriched)
    for category, rows in grouped.items():
        rows.sort(key=lambda row: row["score"]["total"], reverse=True)
        grouped[category] = rows[:topn]
    return grouped


if __name__ == "__main__":
    demo_profile = {
        "client": "中国神华",
        "keywords": ["煤炭", "ESG"],
        "whitelist": ["nea.gov.cn"],
    }
    demo_results = [
        {
            "title": "国家能源局推进煤炭产能储备制度建设",
            "snippet": "ESG 信息披露要求同步细化",
            "url": "https://www.nea.gov.cn/art/2026/10/20/a.html",
            "published_at": "2026-10-20",
            "category": "policy",
        },
        {
            "title": "某自媒体评论短期煤价",
            "snippet": "",
            "url": "https://example.com/post/1",
            "published_at": "2026-08-01",
            "category": "industry",
        },
    ]
    grouped = rank(demo_results, demo_profile, topn=3, threshold=0.5, now=date(2026, 10, 31))
    for category, rows in grouped.items():
        for row in rows:
            print(f"[{category}] total={row['score']['total']} {row['title']}")
    print(f"入选 {sum(len(rows) for rows in grouped.values())} / {len(demo_results)} 条（阈值 0.5）")
