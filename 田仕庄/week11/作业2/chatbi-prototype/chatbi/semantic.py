"""语义层：加载 YAML 定义、构建注册中心与受控词表。"""
from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class SemanticError(Exception):
    """语义层定义错误或 DSL 校验错误。"""


class ValidationError(SemanticError):
    """DSL 校验失败，携带结构化信息供 LLM 修复或前端澄清。"""

    def __init__(self, error: str, hint: str = ""):
        super().__init__(error)
        self.error = error
        self.hint = hint

    def as_dict(self) -> dict:
        return {"error": self.error, "hint": self.hint}


class PermissionDenied(SemanticError):
    """列级权限拒绝。"""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


@dataclass
class JoinEdge:
    to_model: str
    join_type: str
    on: list  # [{"from": 本模型列, "to": 目标模型列}]


@dataclass
class ModelDef:
    name: str
    display_name: str
    physical_table: str
    primary_key: str
    alias: str
    owner: str = ""
    joins: list = field(default_factory=list)


@dataclass
class MetricDef:
    name: str
    display_name: str
    metric_type: str
    model: str
    expression: str
    synonyms: list
    time_model: str
    time_dimension: str
    time_column: str
    available_dimensions: list
    default_filters: list
    description: str
    owner: str = ""


@dataclass
class DimensionDef:
    name: str
    display_name: str
    model: str
    expression: str
    synonyms: list
    dim_type: str
    hierarchy: list
    values: list
    value_mapping: dict
    value_synonyms: dict
    owner: str = ""


_REF_RE = re.compile(r"\$\{([A-Za-z_][\w]*)\}")


def _build_dim_expression(raw: dict) -> str:
    """若维度提供 value_mapping + base_column，则自动生成 CASE 表达式。"""
    if raw.get("expression"):
        return str(raw["expression"])
    vm = raw.get("value_mapping") or {}
    col = raw.get("base_column")
    if vm and col:
        parts = []
        for key, vals in vm.items():
            in_list = ", ".join("'" + str(v).replace("'", "''") + "'" for v in vals)
            parts.append(f"WHEN {col} IN ({in_list}) THEN '{key}'")
        default = raw.get("default_value", "其他")
        return "CASE " + " ".join(parts) + f" ELSE '{default}' END"
    raise SemanticError(f"维度 {raw.get('name')} 缺少 expression 或 base_column")


class SemanticRegistry:
    """语义层注册中心：对象存储 + join 图 + 受控词表索引。"""

    def __init__(self, models, metrics, dimensions):
        self.models = {m.name: m for m in models}
        self.metrics = {m.name: m for m in metrics}
        self.dimensions = {d.name: d for d in dimensions}

        self.metric_terms: dict[str, str] = {}
        self.dim_terms: dict[str, str] = {}
        self.value_terms: dict[str, list] = {}

        self.adjacency: dict[str, list] = {}
        self._build_terms()
        self._build_adjacency()
        self._validate()

    # ---------------- 索引构建 ----------------

    def _build_terms(self):
        for m in self.metrics.values():
            for t in [m.name, m.display_name, *m.synonyms]:
                self.metric_terms.setdefault(str(t).strip().lower(), m.name)
        for d in self.dimensions.values():
            for t in [d.name, d.display_name, *d.synonyms]:
                self.dim_terms.setdefault(str(t).strip().lower(), d.name)
            canonical = set(d.values) | set(d.value_mapping.keys())
            for v in canonical:
                self._add_value_term(str(v).lower(), d.name, str(v))
                for s in d.value_synonyms.get(v, []):
                    self._add_value_term(str(s).lower(), d.name, str(v))

    def _add_value_term(self, term: str, dim: str, canonical: str):
        bucket = self.value_terms.setdefault(term, [])
        if (dim, canonical) not in bucket:
            bucket.append((dim, canonical))

    def _build_adjacency(self):
        """构建无向 join 图：model -> [(邻居模型, 定向条件)]。"""
        for m in self.models.values():
            self.adjacency.setdefault(m.name, [])
        for m in self.models.values():
            for j in m.joins:
                conds = [{"a_col": c["from"], "b_col": c["to"]} for c in j.on]
                self.adjacency[m.name].append((j.to_model, conds))
                self.adjacency.setdefault(j.to_model, []).append(
                    (m.name, [{"a_col": c["b_col"], "b_col": c["a_col"]} for c in conds])
                )

    # ---------------- 校验 ----------------

    def _validate(self):
        for m in self.metrics.values():
            if m.model not in self.models:
                raise SemanticError(f"指标 {m.name} 引用了不存在的模型 {m.model}")
            if m.time_model not in self.models:
                raise SemanticError(f"指标 {m.name} 的时间模型 {m.time_model} 不存在")
            if m.time_dimension not in self.dimensions:
                raise SemanticError(f"指标 {m.name} 的时间维度 {m.time_dimension} 不存在")
            for d in m.available_dimensions:
                if d not in self.dimensions:
                    raise SemanticError(f"指标 {m.name} 引用了不存在的维度 {d}")
                if not self.reachable(m.model, self.dimensions[d].model):
                    raise SemanticError(
                        f"指标 {m.name} 的维度 {d} 所在模型无法从 {m.model} 到达（缺少 join 路径）"
                    )
            if m.metric_type == "composite":
                for ref in _REF_RE.findall(m.expression):
                    if ref not in self.metrics:
                        raise SemanticError(f"复合指标 {m.name} 引用了不存在的指标 {ref}")
                    if not self.reachable(self.models[m.model].name, self.metrics[ref].model):
                        raise SemanticError(f"复合指标 {m.name} 的子指标 {ref} 所在模型不可达")
        for d in self.dimensions.values():
            if d.model not in self.models:
                raise SemanticError(f"维度 {d.name} 引用了不存在的模型 {d.model}")

    def reachable(self, src: str, dst: str) -> bool:
        if src == dst:
            return True
        seen = {src}
        q = deque([src])
        while q:
            u = q.popleft()
            for v, _ in self.adjacency.get(u, []):
                if v == dst:
                    return True
                if v not in seen:
                    seen.add(v)
                    q.append(v)
        return False

    # ---------------- 查询辅助 ----------------

    def metric(self, name: str) -> MetricDef:
        return self.metrics[name]

    def dimension(self, name: str) -> DimensionDef:
        return self.dimensions[name]

    def is_time_dim(self, name: str) -> bool:
        d = self.dimensions.get(name)
        return bool(d and d.dim_type == "time")

    def metric_display(self, name: str) -> str:
        m = self.metrics.get(name)
        return m.display_name if m else name

    def dim_display(self, name: str) -> str:
        d = self.dimensions.get(name)
        return d.display_name if d else name


def _load_yaml(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_yaml_dicts(semantic_dir: Path) -> tuple[list, list, list]:
    """读取 semantic 目录下的原始定义（dict），供建库迁移复用。"""
    semantic_dir = Path(semantic_dir)
    return (
        _load_yaml(semantic_dir / "models.yaml").get("models", []),
        _load_yaml(semantic_dir / "metrics.yaml").get("metrics", []),
        _load_yaml(semantic_dir / "dimensions.yaml").get("dimensions", []),
    )


def model_from_dict(raw: dict) -> ModelDef:
    return ModelDef(
        name=raw["name"], display_name=raw.get("display_name", raw["name"]),
        physical_table=raw["physical_table"], primary_key=raw.get("primary_key", ""),
        alias=raw["alias"], owner=raw.get("owner", ""),
        joins=[JoinEdge(j["to_model"], j.get("join_type", ""), j["on_fields"]) for j in raw.get("joins", [])],
    )


def metric_from_dict(raw: dict) -> MetricDef:
    return MetricDef(
        name=raw["name"], display_name=raw.get("display_name", raw["name"]),
        metric_type=raw["metric_type"], model=raw["model"], expression=raw["expression"],
        synonyms=raw.get("synonyms", []), time_model=raw["time_model"],
        time_dimension=raw["time_dimension"], time_column=raw["time_column"],
        available_dimensions=raw.get("available_dimensions", []),
        default_filters=raw.get("default_filters", []),
        description=raw.get("description", ""), owner=raw.get("owner", ""),
    )


def dimension_from_dict(raw: dict) -> DimensionDef:
    return DimensionDef(
        name=raw["name"], display_name=raw.get("display_name", raw["name"]),
        model=raw["model"], expression=_build_dim_expression(raw),
        synonyms=raw.get("synonyms", []), dim_type=raw.get("dim_type", "categorical"),
        hierarchy=raw.get("hierarchy", []), values=raw.get("values", []),
        value_mapping=raw.get("value_mapping", {}),
        value_synonyms=raw.get("value_synonyms", {}), owner=raw.get("owner", ""),
    )


def build_registry(model_dicts, metric_dicts, dim_dicts) -> SemanticRegistry:
    return SemanticRegistry(
        [model_from_dict(r) for r in model_dicts],
        [metric_from_dict(r) for r in metric_dicts],
        [dimension_from_dict(r) for r in dim_dicts],
    )


def load_registry(semantic_dir: Path) -> SemanticRegistry:
    """从 semantic 目录（YAML）加载语义层定义。"""
    return build_registry(*load_yaml_dicts(semantic_dir))


def load_registry_from_db(store) -> SemanticRegistry | None:
    """从元数据库加载「已发布」语义层定义；无任何已发布对象时返回 None。"""
    rows = store.active_published()
    if not rows:
        return None
    models, metrics, dims = [], [], []
    for r in rows:
        payload = r["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        bucket = {"model": models, "metric": metrics, "dimension": dims}.get(r["obj_type"])
        if bucket is not None:
            bucket.append(payload)
    return build_registry(models, metrics, dims)
