"""Join 图规划：从指标基模型出发，按最短路径补齐所需模型并生成 JOIN 顺序。"""
from __future__ import annotations

from collections import deque

from .semantic import SemanticRegistry, SemanticError


def _shortest_path(base: str, target: str, registry: SemanticRegistry):
    """返回 base->target 的边序列 [(u, v, conds)]；不可达返回 None。"""
    if base == target:
        return []
    prev = {base: None}
    q = deque([base])
    while q:
        u = q.popleft()
        for v, conds in registry.adjacency.get(u, []):
            if v in prev:
                continue
            prev[v] = (u, conds)
            q.append(v)
    if target not in prev:
        return None
    seq = []
    cur = target
    while prev[cur] is not None:
        u, conds = prev[cur]
        seq.append((u, cur, conds))
        cur = u
    seq.reverse()
    return seq


def plan_joins(base_model: str, required_models, registry: SemanticRegistry):
    """规划需要参与的 join，返回按 BFS 序排列的 [(u, v, conds)]。"""
    required = set(required_models) | {base_model}
    needed = set()
    for r in required:
        if r == base_model:
            continue
        path = _shortest_path(base_model, r, registry)
        if path is None:
            raise SemanticError(f"模型 {base_model} 无法通过 join 到达 {r}（缺少 join 路径）")
        for (u, v, _conds) in path:
            needed.add(frozenset({u, v}))

    placed = {base_model}
    q = deque([base_model])
    joins = []
    while q:
        u = q.popleft()
        for v, conds in registry.adjacency.get(u, []):
            if v in placed or frozenset({u, v}) not in needed:
                continue
            placed.add(v)
            q.append(v)
            joins.append((u, v, conds))
    return joins
