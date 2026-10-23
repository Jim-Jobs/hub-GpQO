"""结果摘要：把查询结果转成一句可读的结论。"""
from __future__ import annotations


def _fmt(v):
    if isinstance(v, float):
        return f"{v:,.2f}"
    if isinstance(v, int):
        return f"{v:,}"
    return str(v)


def summarize(dsl: dict, rows: list, registry, resolved_time: tuple = ("", "")) -> str:
    metric = dsl.get("metric")
    dims = list(dsl.get("dimensions") or [])
    mname = registry.metric_display(metric)

    cond = []
    if resolved_time and resolved_time[0]:
        cond.append(f"时间 {resolved_time[0]} ~ {resolved_time[1]}")
    for f in dsl.get("filters") or []:
        val = f.get("value")
        val = "、".join(val) if isinstance(val, (list, tuple)) else val
        cond.append(f"{registry.dim_display(f['dimension'])} = {val}")
    cond_sql = ("（" + "；".join(cond) + "）") if cond else ""

    if not rows:
        return f"当前条件下无数据{cond_sql}。"

    if not dims:
        return f"{mname}为 {_fmt(rows[0][-1])}{cond_sql}。"

    # 不假设结果已排序，按指标值取最大 / 最小
    valued = [(i, r[-1]) for i, r in enumerate(rows) if isinstance(r[-1], (int, float))]
    if valued:
        top = rows[max(valued, key=lambda t: t[1])[0]]
        low = rows[min(valued, key=lambda t: t[1])[0]]
    else:
        top, low = rows[0], rows[-1]

    top_desc = "，".join(f"{registry.dim_display(d)}={_fmt(top[i])}" for i, d in enumerate(dims))
    parts = [f"共 {len(rows)} 组数据{cond_sql}"]
    parts.append(f"{mname}最高的是 {top_desc}（{_fmt(top[-1])}）")
    if len(rows) > 1:
        low_desc = "，".join(f"{registry.dim_display(d)}={_fmt(low[i])}" for i, d in enumerate(dims))
        parts.append(f"最低的是 {low_desc}（{_fmt(low[-1])}）")
    return "；".join(parts) + "。"
