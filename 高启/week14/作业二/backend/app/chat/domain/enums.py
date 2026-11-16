"""问答任务状态和阶段枚举。"""

from enum import Enum


class ChatStatus(str, Enum):
    """表示问答任务对前端可见的生命周期状态。"""

    ACCEPTED = "accepted"
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    CANCELLED = "cancelled"
    FAILED = "failed"


class ChatStage(str, Enum):
    """表示问答任务当前执行或等待的具体阶段。"""

    SCOPE_AND_REWRITE = "scope_and_rewrite"
    RETRIEVAL = "retrieval"
    EVIDENCE_CHECK = "evidence_check"
    ANSWER_GENERATION = "answer_generation"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
