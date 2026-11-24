"""筛选层契约：三维打分 / 阈值过滤 / 每类 Top-N（README §8、§12.1）。

实现 scripts/rank_filter.py 时对齐：
    score(item, profile, now) -> {"relevance", "authority", "timeliness", "total"}
    rank(results, profile, topn=5, threshold=0.4, now=None) -> {category: [item...]}
profile 需包含 keywords: list[str]、whitelist: list[str]（域名白名单）。
"""

import pytest

rank_filter = pytest.importorskip("scripts.rank_filter", reason="scripts 未落地，契约先行")


def make(title, url, published="2026-10-20", category="policy", query=""):
    return {
        "title": title,
        "url": url,
        "published_at": published,
        "category": category,
        "query": query,
    }


def test_whitelist_source_scores_higher_authority(sample_profile, now):
    gov = make("国家能源局发布储能新政", "https://www.nea.gov.cn/a")
    media = make("国家能源局发布储能新政", "https://example.com/a")
    assert (
        rank_filter.score(gov, sample_profile, now)["authority"]
        > rank_filter.score(media, sample_profile, now)["authority"]
    )


def test_recent_item_scores_higher_timeliness(sample_profile, now):
    recent = make("储能新政", "https://a.com/1", published="2026-10-30")
    older = make("储能新政", "https://a.com/2", published="2026-09-05")
    assert (
        rank_filter.score(recent, sample_profile, now)["timeliness"]
        > rank_filter.score(older, sample_profile, now)["timeliness"]
    )


def test_keyword_hits_raise_relevance(sample_profile, now):
    hit = make("煤电与储能协同发展", "https://a.com/1")
    miss = make("域外无关新闻", "https://a.com/2")
    assert (
        rank_filter.score(hit, sample_profile, now)["relevance"]
        > rank_filter.score(miss, sample_profile, now)["relevance"]
    )


def test_query_terms_raise_relevance(sample_profile, now):
    title = "生态环境部发布碳排放核算新规"
    matched = make(title, "https://a.com/1", query="2026-10 政策 生态环境部")
    unmatched = make(title, "https://a.com/2", query="2026-10 陕西煤业 ESG")
    assert (
        rank_filter.score(matched, sample_profile, now)["relevance"]
        > rank_filter.score(unmatched, sample_profile, now)["relevance"]
    )


def test_query_terms_match_snippet(sample_profile, now):
    item = make("行业周报", "https://a.com/1", query="2026-10 绿色矿山 政策")
    item["snippet"] = "本周绿色矿山名单公示，配套政策同步更新。"
    assert rank_filter.score(item, sample_profile, now)["relevance"] > 0


def test_rank_filters_below_threshold(sample_profile, now):
    results = [make("储能新闻", "https://example.com/x")]
    ranked = rank_filter.rank(results, sample_profile, threshold=1.01, now=now)
    assert all(not group for group in ranked.values())


def test_rank_sorted_desc_and_topn_per_category(sample_profile, now):
    results = [
        make(f"储能新闻 {i}", f"https://example.com/{i}", published=f"2026-10-{10 + i:02d}")
        for i in range(7)
    ]
    ranked = rank_filter.rank(results, sample_profile, topn=5, threshold=0.0, now=now)
    policy = ranked.get("policy", [])
    assert len(policy) == 5
    totals = [rank_filter.score(it, sample_profile, now)["total"] for it in policy]
    assert totals == sorted(totals, reverse=True)
    assert all(row["dedup_key"].startswith("https://example.com/") for row in policy)
