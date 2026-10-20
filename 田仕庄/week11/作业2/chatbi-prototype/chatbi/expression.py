"""表达式展开：指标（含复合指标递归引用）与维度表达式。"""
from __future__ import annotations

import re

from .semantic import DimensionDef, SemanticRegistry, SemanticError

_REF_RE = re.compile(r"\$\{([A-Za-z_][\w]*)\}")


def expand_metric(name: str, registry: SemanticRegistry, stack=None) -> str:
    """把指标展开为可嵌入 SQL 的聚合表达式；复合指标递归展开子指标。"""
    if name not in registry.metrics:
        raise SemanticError(f"指标 {name} 不存在")
    metric = registry.metrics[name]
    if metric.metric_type != "composite":
        return f"({metric.expression})"

    stack = list(stack or [])
    if name in stack:
        raise SemanticError(f"指标引用存在循环: {' -> '.join(stack + [name])}")
    stack.append(name)

    def repl(mo):
        ref = mo.group(1)
        return expand_metric(ref, registry, stack)

    return "(" + _REF_RE.sub(repl, metric.expression) + ")"


def dimension_expression(dim: DimensionDef, masked: bool = False) -> str:
    if masked:
        return "'******'"
    return dim.expression
