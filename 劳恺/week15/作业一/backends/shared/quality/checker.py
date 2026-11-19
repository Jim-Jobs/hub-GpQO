"""回复质量检查。

FastAPI 编排器在拿到 Agent 2 的回复后做一次权威检查（README 第 9 节）。
检查不通过时会把具体问题整理成 ``retry_feedback`` 回传给 Agent 2 重试，
**最多重试一次**，再次失败则返回兜底回复。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backends.shared.constants.emotions import EmotionCategory
from backends.shared.constants.fallbacks import FALLBACK_REPLIES
from backends.shared.constants.strategies import (
    ALLOWED_STRATEGIES_BY_CATEGORY,
    DEFAULT_STRATEGY_BY_CATEGORY,
)
from backends.shared.schemas.emotion import EmotionResult
from backends.shared.schemas.response import ResponseResult

MIN_REPLY_LENGTH = 8
MAX_REPLY_LENGTH = 500

#: 回复中不应出现的内部术语或提示词痕迹。
INTERNAL_MARKERS: tuple[str, ...] = (
    "emotion_result",
    "emotion_type",
    "confidence",
    "is_consistent",
    "strategy",
    "json",
    "作为ai",
    "作为一个ai",
    "作为一个人工智能",
    "情绪识别结果",
)


@dataclass(frozen=True)
class QualityIssue:
    """一条质量问题。``severity`` 为 error 时会触发重试。"""

    code: str
    message: str
    severity: str = "error"

    @property
    def is_blocking(self) -> bool:
        return self.severity == "error"


@dataclass
class QualityReport:
    """一次质量检查的结果。"""

    issues: list[QualityIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(issue.is_blocking for issue in self.issues)

    @property
    def blocking_issues(self) -> list[QualityIssue]:
        return [issue for issue in self.issues if issue.is_blocking]

    def feedback(self) -> str:
        """把问题整理成给 Agent 2 的修正提示。"""
        blocking = self.blocking_issues
        if not blocking:
            return ""
        details = "；".join(issue.message for issue in blocking)
        return f"上一次回复未通过质量检查：{details}。请据此修正后重新输出。"

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "issues": [
                {"code": issue.code, "message": issue.message, "severity": issue.severity}
                for issue in self.issues
            ],
        }


def check_response(emotion: EmotionResult, response: ResponseResult) -> QualityReport:
    """检查回复是否可用、是否与情绪一致。"""
    issues: list[QualityIssue] = []
    reply = response.reply.strip()

    if not reply:
        issues.append(QualityIssue("empty_reply", "回复内容为空"))
    elif len(reply) < MIN_REPLY_LENGTH:
        issues.append(
            QualityIssue(
                "reply_too_short",
                f"回复过短（{len(reply)} 字），至少需要 {MIN_REPLY_LENGTH} 字",
            )
        )
    elif len(reply) > MAX_REPLY_LENGTH:
        issues.append(
            QualityIssue(
                "reply_too_long",
                f"回复过长（{len(reply)} 字），建议不超过 {MAX_REPLY_LENGTH} 字",
                severity="warning",
            )
        )

    allowed = ALLOWED_STRATEGIES_BY_CATEGORY.get(
        EmotionCategory(emotion.emotion), set()
    )
    if response.strategy not in allowed:
        allowed_names = "、".join(sorted(strategy.value for strategy in allowed))
        issues.append(
            QualityIssue(
                "strategy_mismatch",
                f"情绪为 {emotion.emotion} 时不应使用 {response.strategy.value} 策略，"
                f"应使用 {allowed_names}",
            )
        )

    if not response.is_consistent:
        issues.append(
            QualityIssue("agent_reported_inconsistent", "Agent 自评回复与情绪不一致")
        )

    lowered = reply.lower()
    leaked = [marker for marker in INTERNAL_MARKERS if marker in lowered]
    if leaked:
        issues.append(
            QualityIssue(
                "internal_leak",
                f"回复中泄露了内部术语：{'、'.join(leaked)}",
            )
        )

    if "{" in reply or "}" in reply:
        issues.append(QualityIssue("format_leak", "回复中混入了 JSON 片段"))

    return QualityReport(issues=issues)


def build_fallback_response(emotion: EmotionResult) -> ResponseResult:
    """质量检查反复不通过时的兜底回复。"""
    category = EmotionCategory(emotion.emotion)
    return ResponseResult(
        strategy=DEFAULT_STRATEGY_BY_CATEGORY[category],
        reply=FALLBACK_REPLIES[category],
        is_consistent=True,
    )
