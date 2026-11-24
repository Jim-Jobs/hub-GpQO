"""契约：复核清单中的字数与表格数量自动统计（README §14）。"""

from __future__ import annotations

import pytest

run = pytest.importorskip("scripts.run")


def test_report_stats_counts_cjk_and_tables():
    markup = (
        "<html><body><table><tr><td>煤炭</td></tr></table>"
        "<p>ESG 治理 2026</p></body></html>"
    )
    stats = run.report_stats(markup)

    assert stats["tables"] == 1
    assert stats["cjk_chars"] == 4
    assert stats["latin_words"] == 2
    assert stats["word_count"] == 6


def test_checklist_reports_word_and_table_counts():
    checklist = run.build_checklist(
        [{"title": "示例", "url": "https://example.com"}],
        gaps=[],
        undated=0,
        month="2026-10",
        stats={"word_count": 5000, "tables": 4},
    )

    assert "字数：5000 字" in checklist
    assert "表格数量：4 张" in checklist


def test_checklist_reads_targets_and_red_lines_from_profile():
    policy = run.ReportPolicy.from_profile({"report_length": 6000, "table_count": "4-5"})
    checklist = run.build_checklist(
        [{"title": "示例", "url": "https://example.com"}],
        gaps=[],
        undated=0,
        month="2026-10",
        stats={"word_count": 6000, "tables": 4},
        policy=policy,
        red_lines=["不评论煤价"],
    )

    assert "目标 5400–6600" in checklist
    assert "目标 4–5" in checklist
    assert "「不评论煤价」" in checklist
