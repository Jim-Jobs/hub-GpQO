"""渲染正文章节：收录统计 / 来源摘要截断 / 信息缺口 / 评级 N/A（README §9）。"""

from datetime import date

import pytest

render_report = pytest.importorskip("scripts.render_report")


def test_section_stats_highlights_and_rating_na(sample_items, sample_profile):
    html = render_report.render(sample_items, sample_profile, now=date(2026, 11, 3))

    assert "本节收录 1 条：高 1 条" in html
    assert "重点影响：" in html
    assert "评级对标" in html and "MSCI ESG 评级：N/A" in html


def test_long_snippet_is_truncated(sample_profile):
    snippet = "储能" * 200
    items = [
        {
            "source": "国家能源局",
            "url": "https://www.nea.gov.cn/a",
            "date": "2026-10-20",
            "title": "长摘要测试",
            "impact_level": "低",
            "impact_one_liner": "测试影响。",
            "snippet": snippet,
            "section": "policy",
        }
    ]
    html = render_report.render(items, sample_profile, now=date(2026, 11, 3))

    assert "…" in html
    assert snippet not in html


def test_gaps_are_rendered(sample_items, sample_profile):
    gaps = [{"query": "2026-10 碳排放 政策 发改委", "category": "policy", "reason": "超时"}]
    html = render_report.render(
        sample_items, sample_profile, now=date(2026, 11, 3), gaps=gaps
    )

    assert "检索缺口（1 个" in html
    assert "超时" in html


def test_iso_datetime_renders_date_only(sample_profile):
    items = [
        {
            "source": "测试来源",
            "url": "https://example.com/a",
            "title": "日期格式测试",
            "impact_level": "低",
            "impact_one_liner": "测试影响。",
            "section": "policy",
            "date": "2026-10-26T15:37:00+08:00",
        }
    ]
    html = render_report.render(items, sample_profile, now=date(2026, 11, 3))

    assert "2026-10-26" in html
    assert "T15:37" not in html


def test_unsafe_url_schemes_are_dropped(sample_profile):
    items = [
        {
            "source": "恶意来源",
            "url": "javascript:alert(1)",
            "title": "协议过滤测试",
            "impact_level": "低",
            "impact_one_liner": "测试影响。",
            "section": "policy",
            "date": "2026-10-20",
        },
        {
            "source": "数据链接来源",
            "url": "data:text/html,<script>alert(1)</script>",
            "title": "数据链接测试",
            "impact_level": "低",
            "impact_one_liner": "测试影响。",
            "section": "policy",
            "date": "2026-10-20",
        },
    ]
    html = render_report.render(items, sample_profile, now=date(2026, 11, 3))

    assert "javascript:" not in html
    assert "data:text/html" not in html
