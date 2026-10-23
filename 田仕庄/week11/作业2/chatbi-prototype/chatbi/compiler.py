"""DSL -> SQL 编译引擎：校验、join 规划、表达式展开、权限注入、SQL 生成。

设计要点：
1. 编译过程完全确定性，不依赖大模型；
2. 权限过滤在编译期强制注入，不接受模型生成的权限条件；
3. 输出参数化的可审计 SQL（仅 SELECT）。
"""
from __future__ import annotations

from datetime import date

from . import planner
from .expression import dimension_expression, expand_metric
from .permission import PermissionContext
from .semantic import (PermissionDenied, SemanticRegistry, ValidationError)
from .timeres import resolve_time_range
from .validator import validate_dsl


def _quote(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _resolve_value(registry: SemanticRegistry, dim_name: str, value):
    """把用户输入的取值规范化到受控词表里的标准取值。"""
    dim = registry.dimension(dim_name)
    allowed = set(dim.values) | set(dim.value_mapping.keys())
    if dim.dim_type != "categorical" or not allowed:
        return value
    s = str(value)
    if s in allowed:
        return s
    for (dn, canon) in registry.value_terms.get(s.lower(), []):
        if dn == dim_name:
            return canon
    raise ValidationError(
        f"维度 {dim_name} 的取值 {s} 不在受控词表内",
        f"合法取值: {', '.join(sorted(allowed))}",
    )


def _predicate(registry: SemanticRegistry, expr: str, dim_name: str, op: str, value) -> str:
    if op in ("eq", "neq", "gt", "gte", "lt", "lte"):
        v = _resolve_value(registry, dim_name, value) if op in ("eq", "neq") else value
        symbol = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        return f"{expr} {symbol} {_quote(v)}"
    if op == "like":
        return f"{expr} LIKE {_quote(value)}"
    if op == "between":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValidationError(f"算子 between 需要两个取值（当前: {value}）")
        return f"{expr} BETWEEN {_quote(value[0])} AND {_quote(value[1])}"
    if op == "in":
        if not isinstance(value, (list, tuple)) or not value:
            raise ValidationError(f"算子 in 的取值必须是非空数组（当前: {value}）")
        vals = [_resolve_value(registry, dim_name, v) for v in value]
        return f"{expr} IN ({', '.join(_quote(v) for v in vals)})"
    raise ValidationError(f"不支持的过滤算子 {op}")


def compile_dsl(dsl: dict, registry: SemanticRegistry,
                permission: PermissionContext | None = None,
                anchor: date | None = None) -> dict:
    """把受控查询 DSL 编译为 SQL。"""
    anchor = anchor or date.today()
    permission = permission or PermissionContext(user_id="anonymous")
    validate_dsl(dsl, registry)

    metric_name = dsl["metric"]
    metric = registry.metrics[metric_name]
    dims = list(dsl.get("dimensions") or [])
    filters = list(dsl.get("filters") or [])
    time_range = dsl.get("time_range")
    order_by = list(dsl.get("order_by") or [])
    limit = dsl.get("limit")

    denied = set(permission.denied_columns)
    masked = set(permission.masked_columns)

    # ---- 列级权限：拒绝 ----
    for d in dims + [f.get("dimension") for f in filters]:
        if d in denied:
            raise PermissionDenied(f"无字段「{registry.dim_display(d)}」的访问权限", d)

    # ---- 需要参与的模型 ----
    required = {metric.model}
    if time_range:
        required.add(metric.time_model)
    for d in dims:
        required.add(registry.dimension(d).model)
    for f in filters:
        required.add(registry.dimension(f["dimension"]).model)
    for rule in permission.row_rules:
        required.add(registry.dimension(rule["dimension"]).model)

    base = registry.models[metric.model]
    joins = planner.plan_joins(metric.model, required, registry)

    # ---- SELECT ----
    select_parts = []
    for d in dims:
        expr = dimension_expression(registry.dimension(d), masked=d in masked)
        select_parts.append(f'{expr} AS "{d}"')
    select_parts.append(f'{expand_metric(metric_name, registry)} AS "{metric_name}"')

    # ---- FROM / JOIN ----
    from_sql = f"{base.physical_table} {base.alias}"
    join_sqls = []
    for (u, v, conds) in joins:
        um = registry.models[u]
        vm = registry.models[v]
        on = " AND ".join(f"{um.alias}.{c['a_col']} = {vm.alias}.{c['b_col']}" for c in conds)
        join_sqls.append(f"JOIN {vm.physical_table} {vm.alias} ON {on}")

    # ---- WHERE ----
    where_parts = [f for f in metric.default_filters]

    if time_range:
        start, end = resolve_time_range(time_range, anchor)
        where_parts.append(f"{metric.time_column} >= {_quote(start)}")
        where_parts.append(f"{metric.time_column} < {_quote(end)}")

    for f in filters:
        dn = f["dimension"]
        expr = dimension_expression(registry.dimension(dn), masked=False)
        where_parts.append(_predicate(registry, expr, dn, f["op"], f.get("value")))

    # ---- 权限行级注入（编译期强制）----
    for rule in permission.row_rules:
        dn = rule["dimension"]
        expr = dimension_expression(registry.dimension(dn), masked=False)
        where_parts.append(_predicate(registry, expr, dn, rule.get("op", "in"), rule.get("values", [])))

    # ---- 组装 ----
    sql = f"SELECT {', '.join(select_parts)} FROM {from_sql}"
    if join_sqls:
        sql += " " + " ".join(join_sqls)
    if where_parts:
        sql += " WHERE " + " AND ".join(f"({w})" for w in where_parts)
    if dims:
        sql += " GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))
    if order_by:
        sql += " ORDER BY " + ", ".join(
            f'"{o["field"]}" {str(o.get("direction", "desc")).upper()}' for o in order_by)
    if limit:
        sql += f" LIMIT {limit}"

    return {
        "sql": sql,
        "metric": metric_name,
        "dimensions": dims,
        "resolved_time_range": resolve_time_range(time_range, anchor) if time_range else ("", ""),
        "joins": [v for (_u, v, _c) in joins],
        "permission_user": permission.user_id,
    }
