"""DSL 校验：编译前拦截非法查询，输出结构化错误供 LLM 修复或前端澄清。"""
from __future__ import annotations

from .semantic import SemanticRegistry, ValidationError

OPS = {"eq", "neq", "in", "gt", "gte", "lt", "lte", "between", "like"}
TIME_TYPES = {"relative", "absolute"}
COMPARE_TYPES = {"mom", "yoy"}
DIRECTIONS = {"asc", "desc"}


def validate_dsl(dsl: dict, registry: SemanticRegistry) -> dict:
    if not isinstance(dsl, dict):
        raise ValidationError("DSL 必须是对象", "示例: {metric, dimensions, filters, time_range}")

    metric_name = dsl.get("metric")
    if not metric_name:
        raise ValidationError("缺少指标 metric", f"可选指标: {', '.join(registry.metrics)}")
    if metric_name not in registry.metrics:
        raise ValidationError(f"指标 {metric_name} 不存在", f"可选指标: {', '.join(registry.metrics)}")
    metric = registry.metrics[metric_name]

    dims = dsl.get("dimensions", []) or []
    if not isinstance(dims, list):
        raise ValidationError("dimensions 必须是数组")
    for d in dims:
        if d not in registry.dimensions:
            raise ValidationError(f"维度 {d} 不存在", f"可选维度: {', '.join(registry.dimensions)}")
        if d not in metric.available_dimensions:
            raise ValidationError(
                f"维度 {d} 不可用于指标 {metric_name}",
                f"该指标可用维度: {', '.join(metric.available_dimensions)}",
            )
    if len(set(dims)) != len(dims):
        raise ValidationError("dimensions 存在重复维度")

    filters = dsl.get("filters", []) or []
    for f in filters:
        if not isinstance(f, dict):
            raise ValidationError("filter 必须是对象")
        dim = f.get("dimension")
        op = f.get("op")
        if dim not in registry.dimensions:
            raise ValidationError(f"过滤维度 {dim} 不存在", f"可选维度: {', '.join(registry.dimensions)}")
        if dim not in metric.available_dimensions:
            raise ValidationError(f"过滤维度 {dim} 不可用于指标 {metric_name}",
                                  f"该指标可用维度: {', '.join(metric.available_dimensions)}")
        if op not in OPS:
            raise ValidationError(f"过滤算子 {op} 不支持", f"可选算子: {', '.join(sorted(OPS))}")
        if "value" not in f:
            raise ValidationError(f"过滤条件 {dim} 缺少 value")
        if op == "in" and not isinstance(f.get("value"), (list, tuple)):
            raise ValidationError(f"算子 in 的 value 必须是数组")

    tr = dsl.get("time_range")
    if tr is not None:
        if not isinstance(tr, dict):
            raise ValidationError("time_range 必须是对象")
        t = tr.get("type")
        if t not in TIME_TYPES:
            raise ValidationError(f"time_range.type 非法: {t}", "可选: relative, absolute")
        if t == "relative" and not tr.get("value"):
            raise ValidationError("相对时间缺少 value")
        if t == "absolute" and not (tr.get("start") and tr.get("end")):
            raise ValidationError("绝对时间必须同时提供 start 与 end")

    cmp_ = dsl.get("compare")
    if cmp_ is not None:
        if cmp_ not in COMPARE_TYPES:
            raise ValidationError(f"compare 非法: {cmp_}", "可选: mom, yoy")
        if dims:
            raise ValidationError("同环比暂不支持带维度分组", "请去掉 dimensions 后重试")

    order_by = dsl.get("order_by", []) or []
    selectable = set(dims) | {metric_name}
    for o in order_by:
        if not isinstance(o, dict) or not o.get("field"):
            raise ValidationError("order_by 元素必须是 {field, direction} 对象")
        if o["field"] not in selectable:
            raise ValidationError(f"排序字段 {o['field']} 不在本次查询中",
                                  f"可排序字段: {', '.join(sorted(selectable))}")
        if o.get("direction", "desc") not in DIRECTIONS:
            raise ValidationError(f"排序方向非法: {o.get('direction')}")

    limit = dsl.get("limit")
    if limit is not None:
        if not isinstance(limit, int) or limit <= 0:
            raise ValidationError("limit 必须是正整数")
    return dsl
