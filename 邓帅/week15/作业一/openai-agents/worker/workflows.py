"""EmotionPipelineWorkflow：每轮对话一次执行（analyze → respond 持久编排）。

- workflow 代码内不直接发起 HTTP（一律经 activity，deterministic 约束）；
- 会话历史由网关 SQLiteSession 持有，history 作为参数传入，
  workflow 不承载对话状态；
- sentiment 事件由 workflow 在两 activity 之间直接发布到流
  （durable log offset 保证先于全部 delta）；
- 重试/超时策略（官方建议，design.md §13）：
  analyze   RetryPolicy(maximum_attempts=3) + ScheduleToClose 60s 限总时长
  respond   StartToClose 120s + heartbeat_timeout 感知取消，attempts=2
  两者均把 4xx 类永久错误列入 non_retryable_error_types。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, TypedDict

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.workflow_streams import WorkflowStream

# sandbox 限制 pathlib/os.path：套根（streams.py）与本目录（activities.py）
# 的 sys.path 由入口 worker/main.py 在进程级设置，这里只做 pass-through 导入
with workflow.unsafe.imports_passed_through():
    from activities import analyze_activity, respond_activity
    from streams import EVENT_TOPIC, StreamEvent


class EmotionPipelineResult(TypedDict):
    """workflow 返回值（网关用于写回会话历史）。"""

    label: str
    reason: str
    response: str


@workflow.defn
class EmotionPipelineWorkflow:
    """一轮「情感识别 → 情感回应」的持久编排。"""

    def __init__(self) -> None:
        # @workflow.init：注册 stream 的 signal/update/query handlers
        self.stream = WorkflowStream()

    @workflow.run
    async def run(
        self, text: str, history: list[dict[str, Any]]
    ) -> EmotionPipelineResult:
        # ① 情感识别（瞬时失败自动重试，最多 3 次 / 总时长 60s）
        analysis = await workflow.execute_activity(
            analyze_activity,
            args=[text, history],
            schedule_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(
                maximum_attempts=3,
                non_retryable_error_types=["PermanentHttpError"],
            ),
        )

        # ② 向流发布 sentiment 事件（在全部 delta 之前）
        self.stream.topic(EVENT_TOPIC, type=StreamEvent).publish(
            StreamEvent(type="sentiment", label=analysis["label"], reason=analysis["reason"])
        )

        # ③ 情感回应（流式 activity：放宽 StartToClose + heartbeat 感知取消）
        full_text = await workflow.execute_activity(
            respond_activity,
            args=[text, analysis["label"], analysis["reason"], history],
            start_to_close_timeout=timedelta(seconds=120),
            heartbeat_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(
                maximum_attempts=2,
                non_retryable_error_types=["PermanentHttpError"],
            ),
        )

        return {
            "label": analysis["label"],
            "reason": analysis["reason"],
            "response": full_text,
        }
