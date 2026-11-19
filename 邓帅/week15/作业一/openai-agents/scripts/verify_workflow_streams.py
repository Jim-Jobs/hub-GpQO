"""任务 1.4：Workflow Streams 最小验证（官方 "Stream LLM output" 模式）。

拓扑（与正式 worker 同构的最小版）：
  - workflow：@workflow.init 构造 WorkflowStream（注册 signal/update/query
    handlers），run 内顺序执行 publish activity；
  - activity：WorkflowStreamClient.from_within_activity() 拿 typed topic，
    async with client（自动批次）逐条 publish 假 token，首包 force_flush；
  - 网关侧：WorkflowStreamClient.create() + subscribe(from_offset=0)，
    事件持久化在 durable log 中，订阅晚于发布也不丢。

前置：temporal server start-dev 已在 :7233 运行。
运行：/opt/miniconda3/envs/py312/bin/python verify_workflow_streams.py
判定：打印的订阅 token 与输入 token 一致且逐条（非整段）到达 -> 可用。
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.contrib.workflow_streams import (
    WorkflowStream,
    WorkflowStreamClient,
)
from temporalio.worker import Worker

TASK_QUEUE = "verify-streams"
BATCH_INTERVAL = timedelta(milliseconds=200)


@dataclass
class Delta:
    """流中传输的事件（与正式实现的 delta 事件同构）。"""

    text: str


@activity.defn
async def publish_tokens(tokens: list[str]) -> str:
    """模拟进程2：分批 publish token 增量，返回全文。"""
    client = WorkflowStreamClient.from_within_activity(
        batch_interval=BATCH_INTERVAL
    )
    topic = client.topic("delta", type=Delta)
    full = []
    async with client:  # 退出时自动 flush 余量
        for i, token in enumerate(tokens):
            topic.publish(Delta(text=token), force_flush=i == 0)
            full.append(token)
            await asyncio.sleep(0.08)  # 模拟 LLM 出 token 节奏
    return "".join(full)


@workflow.defn
class StreamDemoWorkflow:
    """最小编排：单个 activity。"""

    def __init__(self) -> None:
        # @workflow.init：注册 stream 的 signal/update/query handlers
        self.stream = WorkflowStream()

    @workflow.run
    async def run(self, tokens: list[str]) -> str:
        return await workflow.execute_activity(
            publish_tokens,
            tokens,
            start_to_close_timeout=timedelta(seconds=30),
        )


async def main() -> None:
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[StreamDemoWorkflow],
        activities=[publish_tokens],
    )
    worker_task = asyncio.create_task(worker.run())

    tokens = ["情感", "回应", "流水线", "，", "流式", "验证", "成功", "。"]
    handle = await client.start_workflow(
        StreamDemoWorkflow.run,
        tokens,
        id=f"verify-streams-{uuid.uuid4().hex[:8]}",
        task_queue=TASK_QUEUE,
    )

    # 网关侧订阅：durable log 从 offset 0 回放，订阅晚于发布不丢事件
    stream_client = WorkflowStreamClient.create(
        client, handle.id, batch_interval=BATCH_INTERVAL
    )
    received: list[str] = []
    async for item in stream_client.subscribe(["delta"], result_type=Delta):
        received.append(item.data.text)
        print(f"收到 delta: {item.data.text!r}")

    result = await handle.result()
    print(f"workflow 返回全文: {result}")
    print(f"订阅拼接: {''.join(received)}")
    assert "".join(received) == result == "".join(tokens), "token 不一致！"
    print("== Workflow Streams 可用：批次流式 + 订阅一致 ==")

    worker_task.cancel()
    try:
        await worker_task
    except asyncio.CancelledError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
