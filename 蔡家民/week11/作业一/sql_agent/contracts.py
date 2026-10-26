from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class QueryIntent:
    status: str = "answerable"
    metric: str = ""
    dimensions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    time_range: list[str] = field(default_factory=list)
    ranking: str = ""
    grain: str = ""
    assumptions: list[str] = field(default_factory=list)
    clarification: str = ""
    reason: str = ""
    rule_ids: list[str] = field(default_factory=list)


@dataclass
class TableSelection:
    tables: list[str]
    columns: dict[str, list[str]] = field(default_factory=dict)
    joins: list[str] = field(default_factory=list)
    reason: str = ""
    bridge_tables: list[str] = field(default_factory=list)


@dataclass
class SQLCandidate:
    sql: str
    params: dict[str, Any] = field(default_factory=dict)
    explanation: str = ""
    changes: str = ""


@dataclass
class Check:
    name: str
    passed: bool
    error_type: str = ""
    evidence: str = ""
    retryable: bool = True


@dataclass
class ValidationReport:
    passed: bool
    checks: list[Check]


@dataclass
class ExecutionResult:
    status: str
    columns: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    truncated: bool = False
    elapsed_ms: float = 0
    error_type: str = ""
    error: str = ""


@dataclass
class FinalAnswer:
    text: str
    facts: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass
class WorkflowResult:
    run_id: str
    question: str
    status: str
    mode: str
    reference_date: str
    intent: QueryIntent | None = None
    selection: TableSelection | None = None
    candidates: list[SQLCandidate] = field(default_factory=list)
    validations: list[ValidationReport] = field(default_factory=list)
    execution: ExecutionResult | None = None
    answer: FinalAnswer | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
