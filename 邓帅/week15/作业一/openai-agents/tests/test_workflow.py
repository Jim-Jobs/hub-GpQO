"""任务 8.3b（openai-agents 套）：workflow 编排测试 —— WorkflowEnvironment。

- 时间跳跃（time-skipping）测试环境，不起真 server；
- 以同名假 activity 注册到 worker（重放编排语义而非 HTTP 细节）；
- 断言：analyze 先于 respond；analyze 瞬时失败按 RetryPolicy 自动重试。
"""

from __future__ import annotations

import uuid

from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker
from workflows import EmotionPipelineWorkflow

TASK_QUEUE = "test-emotion"


def _make_fakes(fail_first_analyze: bool):
    """同名假 activity：闭包记录调用顺序与次数。"""
    calls: list[str] = []

    async def analyze_activity(text: str, history: list) -> dict:
        calls.append("analyze")
        if fail_first_analyze and len(calls) == 1:
            raise RuntimeError("模拟瞬时失败")
        return {"label": "positive", "reason": "假依据"}

    async def respond_activity(text: str, label: str, reason: str, history: list) -> str:
        calls.append("respond")
        return "假回应全文"

    return calls, [activity.defn(analyze_activity), activity.defn(respond_activity)]


async def _run_workflow(env: WorkflowEnvironment, fakes, text: str, history: list):
    _, activities_ = fakes
    worker = Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[EmotionPipelineWorkflow],
        activities=activities_,
    )
    async with worker:
        return await env.client.execute_workflow(
            EmotionPipelineWorkflow.run,
            args=[text, history],
            id=f"test-{uuid.uuid4().hex[:8]}",
            task_queue=TASK_QUEUE,
        )


async def test_workflow_orders_analyze_then_respond():
    env = await WorkflowEnvironment.start_time_skipping()
    async with env:
        calls, fakes = _make_fakes(fail_first_analyze=False)
        result = await _run_workflow(env, (calls, fakes), "今天很开心", [])
        assert result["label"] == "positive"
        assert result["response"] == "假回应全文"
        assert calls == ["analyze", "respond"]  # 编排顺序


async def test_workflow_retries_transient_analyze_failure():
    env = await WorkflowEnvironment.start_time_skipping()
    async with env:
        calls, fakes = _make_fakes(fail_first_analyze=True)
        result = await _run_workflow(env, (calls, fakes), "今天很开心", [])
        # 第 1 次 analyze 失败 → RetryPolicy 自动重试 → 第 2 次成功 → respond
        assert calls == ["analyze", "analyze", "respond"]
        assert result["response"] == "假回应全文"
