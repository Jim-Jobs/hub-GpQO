"""文档解析状态定义（库内用英文码，界面展示中文标签）。"""

from __future__ import annotations

STATUS_PENDING = "pending"
STATUS_PARSING = "parsing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"

STATUS_LABELS: dict[str, str] = {
    STATUS_PENDING: "待解析",
    STATUS_PARSING: "解析中",
    STATUS_COMPLETED: "解析完成",
    STATUS_FAILED: "解析失败",
}

# 解析流水线上的阶段（展示给用户的“解析中”细分进度）
STAGE_QUEUED = "排队中"
STAGE_PARSING = "解析文档"
STAGE_CHUNKING = "切分文本"
STAGE_EMBEDDING = "向量化"
STAGE_INDEXING = "写入向量库"
STAGE_DONE = "解析完成"
STAGE_FAILED = "解析失败"

# 每个阶段对应的大致进度（0-100），用于进度条
STAGE_PROGRESS: dict[str, int] = {
    STAGE_QUEUED: 0,
    STAGE_PARSING: 10,
    STAGE_CHUNKING: 55,
    STAGE_EMBEDDING: 70,
    STAGE_INDEXING: 90,
    STAGE_DONE: 100,
}


def status_label(status: str | None) -> str:
    return STATUS_LABELS.get(status or "", status or "未知")
