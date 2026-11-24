"""中间产物契约：pydantic 模型定义与 JSON Schema 导出（README §12.1）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0"
SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
IMPACT_LEVELS = ("高", "中", "低", "待核实")
ImpactLevel = Literal["高", "中", "低", "待核实"]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Window(ContractModel):
    start: str
    end: str


class Gap(ContractModel):
    query: str
    category: str
    reason: str


class RawResult(ContractModel):
    title: str
    url: str
    snippet: str
    published_at: str
    source: str
    query: str
    category: str
    api: str
    fetched_at: str

    @classmethod
    def from_mapping(cls, data: dict) -> RawResult:
        return cls(**{name: str(data.get(name) or "") for name in cls.model_fields})


class RawArtifact(ContractModel):
    schema_version: str
    generated_at: str
    api: str
    window: Window
    gaps: list[Gap]
    results: list[RawResult]


class Score(ContractModel):
    relevance: float
    authority: float
    timeliness: float
    total: float


class RankedResult(RawResult):
    score: Score
    dedup_key: str

    @classmethod
    def from_mapping(cls, data: dict) -> RankedResult:
        base = {name: str(data.get(name) or "") for name in RawResult.model_fields}
        return cls(
            **base,
            score=Score.model_validate(data.get("score") or {}),
            dedup_key=str(data.get("dedup_key") or ""),
        )


class RankedArtifact(ContractModel):
    schema_version: str
    generated_at: str
    threshold: float
    topn: int
    groups: dict[str, list[RankedResult]]


class SummarizedItem(RawResult):
    date: str
    section: str
    impact_level: ImpactLevel
    impact_one_liner: str
    todo: bool = False

    @classmethod
    def from_mapping(cls, data: dict) -> SummarizedItem:
        base = {name: str(data.get(name) or "") for name in RawResult.model_fields}
        return cls(
            **base,
            date=str(data.get("date") or ""),
            section=str(data.get("section") or ""),
            impact_level=cast(ImpactLevel, str(data.get("impact_level") or "")),
            impact_one_liner=str(data.get("impact_one_liner") or ""),
            todo=bool(data.get("todo", False)),
        )


class SummarizedArtifact(ContractModel):
    schema_version: str
    generated_at: str
    model: str
    items: list[SummarizedItem]


ARTIFACTS: dict[str, type[BaseModel]] = {
    "raw": RawArtifact,
    "ranked": RankedArtifact,
    "summarized": SummarizedArtifact,
}


def json_schema(model: type[BaseModel]) -> dict:
    return {"$schema": SCHEMA_DRAFT, **model.model_json_schema()}


def as_dict(obj: BaseModel) -> dict:
    return obj.model_dump(mode="json")


def export_schemas(directory: Path = SCHEMA_DIR) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, model in ARTIFACTS.items():
        path = directory / f"{name}.schema.json"
        payload = json.dumps(json_schema(model), ensure_ascii=False, indent=2) + "\n"
        path.write_text(payload, encoding="utf-8")
        paths.append(path)
    return paths


if __name__ == "__main__":
    for exported in export_schemas():
        print(exported)
    sample = RawResult.from_mapping({"title": "演示条目", "url": "https://example.com/a"})
    print("示例原始条目：", sample)
    print("RankedResult 字段：", list(RankedResult.model_fields))
