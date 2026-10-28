"""SQL Agent 的 Pydantic 数据契约。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    """表选择 Agent 的结构化结果。"""

    tables: list[str] = Field(description="回答该问题所需的最少表名集合")
    # reason/sql/answer 给默认值：模型偶发漏字段时降级走修复/澄清/表格路径，
    # 而不是让 pydantic ValidationError（含 errors.pydantic.dev 链接）直达用户。
    reason: str = Field(default="", description="选择这些表的理由")
    confidence: float = Field(default=0.0, ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None


class SQLDraft(BaseModel):
    """SQL 生成 / 改写 Agent 的结构化结果。"""

    sql: str = Field(default="", description="单条 SQLite SELECT 或 WITH ... SELECT 语句")
    explanation: str = Field(default="", description="SQL 如何回答用户问题")
    assumptions: list[str] = Field(default_factory=list)
    cannot_fix: bool = Field(default=False, description="改写时是否无法修复")


class ErrorCode(str, Enum):
    UNSAFE = "unsafe"
    SYNTAX = "syntax"
    UNKNOWN_OBJECT = "unknown_object"
    AMBIGUOUS = "ambiguous"
    RUNTIME = "runtime"
    TIMEOUT = "timeout"
    EMPTY = "empty"

    # Python 3.11+ 起 str-Enum 的 __format__ 会输出 "ErrorCode.SYNTAX"，
    # 导致 prompt 和日志里的错误码带着类名前缀。显式复用 str 的实现修正，
    # 使 str() / f-string / format() / %s 全部返回 "syntax"。
    __str__ = str.__str__


class ValidationResult(BaseModel):
    ok: bool
    code: ErrorCode | None = None
    message: str = ""
    normalized_sql: str = ""


class ExecutionResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False


class SQLAttempt(BaseModel):
    sql: str
    source: str
    code: ErrorCode | None = None
    error: str = ""


class FinalAnswer(BaseModel):
    """结果汇总 Agent 的结构化结果。"""

    answer: str = Field(default="", description="面向用户的自然语言答案")
    evidence: list[str] = Field(default_factory=list, description="支撑答案的具体数值")
    caveats: list[str] = Field(default_factory=list)


class QueryState(BaseModel):
    """一次查询的完整状态，由 Orchestrator 维护。"""

    question: str
    selection: TableSelection | None = None
    schema_context: str = ""
    sql: str | None = None
    attempts: list[SQLAttempt] = Field(default_factory=list)
    validation: ValidationResult | None = None
    execution: ExecutionResult | None = None
    answer: FinalAnswer | None = None
    repair_attempts: int = 0
    stage: str = "init"


class WorkflowOutput(BaseModel):
    """面向用户的最终 JSON 结构，由代码组装。"""

    question: str
    answer: str
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    repair_attempts: int = 0
    evidence: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    error: str | None = None
