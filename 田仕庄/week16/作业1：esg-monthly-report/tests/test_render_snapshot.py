"""渲染层快照：Jinja2 输出与基线一致（README §13）。

实现 scripts/render_report.py 时对齐：
    render(items, profile, now) -> str        # HTML 字符串
快照基线：tests/snapshots/monthly_report.html（渲染器落地后生成并人工审校）。
"""

from datetime import date
from pathlib import Path

import pytest

render_report = pytest.importorskip("scripts.render_report", reason="scripts 未落地，契约先行")

SNAPSHOT = Path(__file__).parent / "snapshots" / "monthly_report.html"


def test_render_matches_snapshot(sample_items, sample_profile):
    if not SNAPSHOT.exists():
        pytest.skip(f"快照基线缺失：{SNAPSHOT.name}；模板与渲染器落地后生成")
    html = render_report.render(sample_items, sample_profile, now=date(2026, 11, 3))
    assert html == SNAPSHOT.read_text(encoding="utf-8")
