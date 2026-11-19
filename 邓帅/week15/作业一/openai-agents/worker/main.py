"""Temporal worker 启动入口（独立进程，无 HTTP 端口）。

拓扑（design.md D9）：worker 与网关分进程——网关 = Temporal client +
FastAPI + SSE；worker = workflow 定义 + 两 activity。经 task queue
"emotion-oa" 接活。

运行：/opt/miniconda3/envs/py312/bin/python main.py（scripts/start_all.sh 拉起）
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

# 进程级导入路径：worker/ 目录（activities/workflows）+ 套根（streams/contracts）
# （workflows.py 在 sandbox 内运行，不能自己做 sys.path/pathlib 操作）
_WORKER_DIR = str(Path(__file__).resolve().parent)
_SUITE_ROOT = str(Path(__file__).resolve().parents[1])
for _p in (_WORKER_DIR, _SUITE_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from activities import analyze_activity, respond_activity
from workflows import EmotionPipelineWorkflow

TEMPORAL_ADDRESS = os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
TASK_QUEUE = os.getenv("TASK_QUEUE", "emotion-oa")


async def main() -> None:
    client = await Client.connect(TEMPORAL_ADDRESS)
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[EmotionPipelineWorkflow],
        activities=[analyze_activity, respond_activity],
    )
    print(f"worker 启动：task_queue={TASK_QUEUE} temporal={TEMPORAL_ADDRESS}")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
