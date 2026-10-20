"""语义层对象 payload 的强类型校验模型（Pydantic v2）。

写库前必须经过 ``validate_payload``；读取后可再用同一模型反序列化。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .enums import ObjType


class FilterSpec(BaseModel):
    dimension: str
    op: Literal["eq", "neq", "in", "gt", "lt", "between", "like"] = "eq"
    value: object


class JoinField(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: str = Field(alias="from")
    to: str


class JoinSpec(BaseModel):
    to_model: str
    type: Literal["many_to_one", "one_to_many", "many_to_many"]
    on: list[JoinField]


class ModelPayload(BaseModel):
    display_name: str
    physical_table: str
    primary_key: str | None = None
    time_column: str | None = None
    joins: list[JoinSpec] = Field(default_factory=list)
    default_filters: list[FilterSpec] = Field(default_factory=list)
    owner: str = ""


class MetricFormat(BaseModel):
    unit: str = ""
    decimals: int = 2


class MetricPayload(BaseModel):
    display_name: str
    metric_type: Literal["atomic", "composite"]        # derived 本期不支持
    model: str
    expression: str
    synonyms: list[str] = Field(default_factory=list)
    time_dimension: str | None = None
    time_column: str | None = None
    available_dimensions: list[str] = Field(default_factory=list)
    default_filters: list[FilterSpec] = Field(default_factory=list)
    format: MetricFormat = Field(default_factory=MetricFormat)
    description: str = ""
    owner: str = ""


class DimensionPayload(BaseModel):
    display_name: str
    model: str
    expression: str | None = None
    dim_type: Literal["categorical", "time", "numeric"] = "categorical"
    hierarchy: list[str] = Field(default_factory=list)
    value_mapping: dict[str, list[str]] = Field(default_factory=dict)
    synonyms: list[str] = Field(default_factory=list)
    owner: str = ""


PAYLOAD_MODELS: dict[ObjType, type[BaseModel]] = {
    ObjType.model: ModelPayload,
    ObjType.metric: MetricPayload,
    ObjType.dimension: DimensionPayload,
}


def validate_payload(obj_type: ObjType, payload: dict) -> dict:
    """校验并归一化 payload；校验失败直接抛 ValidationError，不允许写库。"""
    return PAYLOAD_MODELS[obj_type].model_validate(payload).model_dump(by_alias=True, exclude_none=True)
