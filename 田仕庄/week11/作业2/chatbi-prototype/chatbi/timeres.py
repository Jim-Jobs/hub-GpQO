"""时间表达式解析：相对/绝对时间 -> [start, end) 日期区间。"""
from __future__ import annotations

import re
from datetime import date, timedelta


def _month_start(d: date) -> date:
    return date(d.year, d.month, 1)


def _add_months(d: date, n: int) -> date:
    y = d.year + (d.month - 1 + n) // 12
    m = (d.month - 1 + n) % 12 + 1
    return date(y, m, 1)


def _quarter_start(d: date, q: int) -> date:
    return date(d.year, (q - 1) * 3 + 1, 1)


def resolve_time_range(spec: dict, anchor: date) -> tuple[str, str]:
    """把 DSL 的 time_range 解析为具体的 [start, end) ISO 日期字符串。

    返回的日期字符串可直接用于与 datetime 列做字符串比较。
    """
    if not spec:
        return "", ""
    t = spec.get("type")
    if t == "absolute":
        start = str(spec.get("start", ""))
        end = str(spec.get("end", ""))
        if not start or not end:
            raise ValueError("绝对时间需要同时提供 start 与 end")
        return start, end
    if t != "relative":
        raise ValueError(f"不支持的时间类型: {t}")

    v = str(spec.get("value", ""))
    m = re.fullmatch(r"last_(\d+)_(days|months|years)", v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if n <= 0:
            raise ValueError("时间跨度必须为正整数")
        if unit == "days":
            return (anchor - timedelta(days=n - 1)).isoformat(), (anchor + timedelta(days=1)).isoformat()
        if unit == "months":
            return _add_months(_month_start(anchor), -(n - 1)).isoformat(), _add_months(_month_start(anchor), 1).isoformat()
        return date(anchor.year - (n - 1), 1, 1).isoformat(), date(anchor.year + 1, 1, 1).isoformat()

    if v == "today":
        return anchor.isoformat(), (anchor + timedelta(days=1)).isoformat()
    if v == "yesterday":
        return (anchor - timedelta(days=1)).isoformat(), anchor.isoformat()
    if v in ("this_week", "last_week"):
        monday = anchor - timedelta(days=anchor.weekday())
        if v == "last_week":
            monday -= timedelta(days=7)
        return monday.isoformat(), (monday + timedelta(days=7)).isoformat()
    if v == "this_month":
        return _month_start(anchor).isoformat(), _add_months(_month_start(anchor), 1).isoformat()
    if v == "last_month":
        return _add_months(_month_start(anchor), -1).isoformat(), _month_start(anchor).isoformat()
    if v in ("this_quarter", "last_quarter"):
        q = (anchor.month - 1) // 3 + 1
        if v == "last_quarter":
            q -= 1
            if q == 0:
                return date(anchor.year - 1, 10, 1).isoformat(), date(anchor.year, 1, 1).isoformat()
        s = _quarter_start(anchor, q)
        return s.isoformat(), _add_months(s, 3).isoformat()
    if v == "this_year":
        return date(anchor.year, 1, 1).isoformat(), date(anchor.year + 1, 1, 1).isoformat()
    if v == "last_year":
        return date(anchor.year - 1, 1, 1).isoformat(), date(anchor.year, 1, 1).isoformat()

    raise ValueError(f"不支持的时间取值: {v}")


def previous_period(spec: dict, anchor: date) -> dict:
    """把时间区间前移一个等长周期，用于环比/同比对比。"""
    start, end = resolve_time_range(spec, anchor)
    s = date.fromisoformat(start)
    e = date.fromisoformat(end)
    if str(spec.get("value", "")).endswith("year") or spec.get("value") in ("this_year", "last_year"):
        return {"type": "absolute",
                "start": date(s.year - 1, s.month, s.day).isoformat(),
                "end": date(e.year - 1, e.month, e.day).isoformat()}
    span = (e - s).days
    prev_start = s - timedelta(days=span)
    return {"type": "absolute", "start": prev_start.isoformat(), "end": s.isoformat()}


def describe_time_range(spec: dict, anchor: date) -> str:
    if not spec:
        return "全部时间"
    start, end = resolve_time_range(spec, anchor)
    return f"{start} ~ {end}"
