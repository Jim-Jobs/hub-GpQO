"""定义黄金用例、评分输出和可复核评测报告的数据结构。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


EvaluationCategory = Literal[
    "FQA",
    "DOC-QA",
    "TableQA",
    "KBQA",
    "TaskQA",
    "scope",
    "multimodal_input",
    "multimodal_output",
]
ExpectedBehavior = Literal[
    "answer",
    "answer_with_limit",
    "report_conflict",
    "capability_boundary",
    "answer_and_guide",
    "abstain",
    "answer_from_text_and_image",
    "mixed_media",
]


class EvaluationCase(BaseModel):
    """从 JSONL 读取的一条固定黄金问答及其确定性预期。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    category: EvaluationCategory
    question: str = Field(min_length=1)
    expected_route: Literal["in_scope", "out_of_scope"]
    expected_behavior: ExpectedBehavior
    required_answer_points: list[str] = Field(min_length=1)
    evidence_pages: list[int] = Field(default_factory=list)
    input_image: str | None = None
    assertions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_behavior_contract(self) -> EvaluationCase:
        """检查路由、行为类别和图像 fixture 标记之间的组合约束。"""
        if self.expected_behavior == "answer_and_guide" and self.expected_route != "out_of_scope":
            raise ValueError("answer_and_guide 用例必须标记为 out_of_scope")
        if self.expected_behavior == "abstain" and self.expected_route != "in_scope":
            raise ValueError("abstain 用例必须属于资料范围并经过证据检查")
        if self.expected_route == "out_of_scope" and self.evidence_pages:
            raise ValueError("out_of_scope 用例不能标注 PDF 证据页")
        if self.category == "multimodal_input" and self.input_image is None:
            raise ValueError("multimodal_input 用例必须指定图片 fixture")
        if self.category != "multimodal_input" and self.input_image is not None:
            raise ValueError("只有 multimodal_input 用例可以指定用户图片 fixture")
        if self.category == "multimodal_output" and self.expected_behavior != "mixed_media":
            raise ValueError("multimodal_output 用例必须要求 mixed_media 回答")
        return self


class DimensionScores(BaseModel):
    """保存四个评分维度的 0–5 分；不适用维度使用 None。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    correctness: int | None = Field(ge=0, le=5)
    grounding: int | None = Field(ge=0, le=5)
    completeness: int | None = Field(ge=0, le=5)
    behavior: int | None = Field(ge=0, le=5)


class ScoringResult(BaseModel):
    """保存评分模型的维度分、硬失败信号和可复核理由。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    case_id: str = Field(min_length=1)
    dimension_scores: DimensionScores
    fatal_flags: list[str]
    missing_points: list[str]
    unsupported_claims: list[str]
    evidence_refs: list[str]
    rationale: str = Field(min_length=1)


class EvaluationCaseResult(BaseModel):
    """合并单条用例的运行 trace、确定性检查和评分结果。"""

    case_id: str
    category: EvaluationCategory
    trace: dict
    deterministic_checks: dict[str, bool | None]
    scoring: ScoringResult | None = None
    weighted_score: float | None = None
    score_passed: bool = False
    passed: bool = False
    error: str | None = None


class MetricResult(BaseModel):
    """记录一个可复核比例指标的分子、分母和比率。"""

    numerator: int
    denominator: int
    value: float | None


class EvaluationRunReport(BaseModel):
    """记录一次黄金集运行的模型、数据版本、指标和逐条结果。"""

    run_id: str
    started_at: str
    completed_at: str
    document_id: str
    dataset_sha256: str
    answer_model: str
    scoring_model: str
    prompt_version: str
    case_count: int
    metrics: dict[str, MetricResult]
    category_pass_rates: dict[str, MetricResult]
    failed_case_ids: list[str]
    passed: bool
    cases: list[EvaluationCaseResult]
