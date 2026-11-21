"""Redis Streams 消费入口；识别和回应分别由两个容器运行。"""

import argparse
import asyncio
import logging
import os
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from redis.asyncio import Redis
from redis.exceptions import ResponseError

from backend.config import load_environment
from backend.events import publish_event
from backend.schemas import (
    ChatResult,
    CompletedEvent,
    FailureEvent,
    ProgressEvent,
    RedisChatJob,
    RedisSentimentResult,
    Sentiment,
)
from backend.workflow import build_reply_workflow, build_sentiment_workflow

REQUESTS = "chat:requests"
SENTIMENTS = "chat:sentiments"
logger = logging.getLogger(__name__)
load_environment()


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    """确保 Redis Stream 的工作消费组存在。

    Args:
        redis: 当前工作进程的异步 Redis 客户端。
        stream: 要消费的 Stream 名称。
        group: 消费组名称。

    Returns:
        消费组存在或创建成功后无返回值。

    Raises:
        redis.exceptions.ResponseError: 创建失败且不是重复组错误。
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


async def consume(kind: str) -> None:
    """消费一个工作流阶段的 Redis 消息并发布下一阶段结果。

    Args:
        kind: 工作进程角色，只能是 `sentiment` 或 `reply`。

    Returns:
        消费循环持续运行，不会主动返回。

    Raises:
        ValueError: 工作进程角色不受支持。
        redis.exceptions.RedisError: Redis 连接或消息操作失败。
    """
    if kind not in {"sentiment", "reply"}:
        raise ValueError("kind 必须是 sentiment 或 reply")

    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    input_stream, output_stream, group = (
        (REQUESTS, SENTIMENTS, "sentiment") if kind == "sentiment"
        else (SENTIMENTS, "", "reply")
    )
    try:
        await ensure_group(redis, input_stream, group)
        graph = build_sentiment_workflow() if kind == "sentiment" else build_reply_workflow()
        while True:
            batches = await redis.xreadgroup(group, kind, {input_stream: ">"}, count=1, block=5000)
            for _, entries in batches:
                for message_id, fields in entries:
                    await _process_message(redis, graph, kind, fields)
                    await redis.xack(input_stream, group, message_id)
    finally:
        await redis.aclose()


async def _process_message(redis: Redis, graph: Any, kind: str, fields: dict[str, str]) -> None:
    """处理单条工作消息，并发布进度、后续任务或最终结果。

    Args:
        redis: 当前工作进程的异步 Redis 客户端。
        graph: 当前进程已编译的 LangGraph。
        kind: 当前进程角色，值为 `sentiment` 或 `reply`。
        fields: Redis Stream 中存放 request ID 和 JSON payload 的字段。

    Returns:
        消息处理完成后无返回值。

    Raises:
        redis.exceptions.RedisError: Redis 消息写入失败。
    """
    request_id: UUID | None = UUID(fields["request_id"]) if fields.get("request_id") else None
    stage = "sentiment_processing" if kind == "sentiment" else "reply_processing"
    try:
        if kind == "sentiment":
            job = RedisChatJob.model_validate_json(fields["payload"])
            request_id = job.request_id
            await publish_event(redis, ProgressEvent(
                event_type="progress", request_id=request_id,
                stage="sentiment_processing", message="正在识别情感…",
            ))
            result = await asyncio.to_thread(graph.invoke, job.model_dump(mode="json"))
            message = RedisSentimentResult(
                request_id=request_id,
                text=job.text,
                sentiment=result["sentiment"],
                reason=result["reason"],
            )
            await redis.xadd(SENTIMENTS, {
                "request_id": str(request_id),
                "payload": message.model_dump_json(),
            })
        else:
            message = RedisSentimentResult.model_validate_json(fields["payload"])
            request_id = message.request_id
            await publish_event(redis, ProgressEvent(
                event_type="progress", request_id=request_id,
                stage="reply_processing", message="正在生成回复…",
            ))
            result = await asyncio.to_thread(graph.invoke, message.model_dump(mode="json"))
            completed = ChatResult(
                request_id=request_id,
                sentiment=message.sentiment,
                reason=message.reason,
                reply=result["reply"],
            )
            await publish_event(redis, CompletedEvent(
                event_type="completed", request_id=request_id, result=completed,
            ))
    except Exception as error:
        missing_key = error.args[0] if isinstance(error, KeyError) and error.args else "未知"
        logger.warning(
            "工作阶段失败：kind=%s error_type=%s missing_key=%s",
            kind,
            type(error).__name__,
            missing_key if isinstance(error, KeyError) else "不适用",
        )
        if request_id is not None:
            code = "invalid_sentiment_output" if isinstance(error, ValidationError) and kind == "sentiment" else "agent_error"
            await publish_event(redis, FailureEvent(
                event_type="failed",
                request_id=request_id,
                failed_stage=stage,  # type: ignore[arg-type]
                error_code=code,
                message="情感识别失败" if kind == "sentiment" else "回复生成失败",
            ))


def main() -> None:
    """解析命令行角色并启动对应的消费者进程。

    Args:
        无；工作进程角色从命令行位置参数读取。

    Returns:
        消费循环正常结束后无返回值。

    Raises:
        SystemExit: 命令行参数缺失或无效。
        Exception: Redis 消费循环启动或运行失败。
    """
    parser = argparse.ArgumentParser(description="启动 LangGraph 工作进程")
    parser.add_argument("kind", choices=("sentiment", "reply"))
    args = parser.parse_args()
    try:
        asyncio.run(consume(args.kind))
    except KeyboardInterrupt:
        print("收到 Ctrl+C，worker 已停止并关闭 Redis 连接。")


if __name__ == "__main__":
    main()
