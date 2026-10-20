"""图表推荐：根据维度形态选择图表类型。"""
from __future__ import annotations


def recommend(dsl: dict, columns: list, rows: list, registry) -> dict:
    dims = list(dsl.get("dimensions") or [])
    metric = dsl.get("metric")

    if not dims:
        value = rows[0][-1] if rows else None
        return {"type": "number", "value": value, "label": metric}

    if len(dims) == 1:
        dim = dims[0]
        if registry.is_time_dim(dim):
            return {"type": "line", "x": dim, "y": metric}
        n = len(rows)
        if n <= 8:
            return {"type": "pie", "x": dim, "y": metric}
        return {"type": "bar", "x": dim, "y": metric}

    if len(dims) == 2:
        time_dims = [d for d in dims if registry.is_time_dim(d)]
        if time_dims:
            other = [d for d in dims if d not in time_dims][0]
            return {"type": "line", "x": "time_dims", "y": metric, "series": other}
        return {"type": "bar", "x": dims[0], "y": metric, "series": dims[1]}

    return {"type": "table", "x": dims, "y": metric}
