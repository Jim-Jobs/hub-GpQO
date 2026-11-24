"""时间窗口归属契约（README §8.1）。

实现 scripts/search.py 时对齐：
    window_bounds(month) -> (date, date)          # 主窗口起始日前 30 天 ~ 主窗口月末
    event_in_window(event_date, month) -> bool
"""

from datetime import date

import pytest

search = pytest.importorskip("scripts.search", reason="scripts 未落地，契约先行")


class TestWindowBounds:
    def test_bounds_cover_supplement_window(self):
        assert search.window_bounds("2026-10") == (date(2026, 9, 1), date(2026, 10, 31))


class TestAttribution:
    def test_in_main_window(self):
        assert search.event_in_window(date(2026, 10, 15), "2026-10")

    def test_in_supplement_window(self):
        assert search.event_in_window(date(2026, 9, 10), "2026-10")

    def test_before_window_excluded(self):
        assert not search.event_in_window(date(2026, 8, 31), "2026-10")

    def test_after_window_excluded(self):
        assert not search.event_in_window(date(2026, 11, 1), "2026-10")

    def test_boundary_days_included(self):
        assert search.event_in_window(date(2026, 9, 1), "2026-10")
        assert search.event_in_window(date(2026, 10, 31), "2026-10")
