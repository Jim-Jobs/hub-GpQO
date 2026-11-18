"""运行 Redis Streams 情感识别和情绪回应两个独立消费者。"""

import argparse
import asyncio
import json
import logging
import os
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from backend.config import load_environment
from backend.schemas import (
    ChatResult,
    CompletedEvent,
    FailureEvent,
    ProgressEvent,
    RedisChatJob,
    RedisSentimentResult,
    WorkflowEvent,
)
from backend.workflow import generate_reply, identify_sentiment

REQUESTS = "chat:requests"
SENTIMENTS = "chat:sentiments"
EVENTS_PREFIX = "chat:events:"
EVENT_TTL_SECONDS = 300
logger = logging.getLogger(__name__)
load_environment()


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    """确保请求流已创建对应消费组。

    Args:
        redis: 当前进程使用的异步 Redis 客户端。
        stream: 请求流名称。
        group: 消费组名称。

    Returns:
        消费组已存在或成功创建时无返回值。

    Raises:
        ResponseError: 创建消费组失败且不是重复组错误。
    """
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as error:
        if "BUSYGROUP" not in str(error):
            raise


async def publish_event(redis: Redis, event: WorkflowEvent) -> None:
    """将工作流事件写入该请求的 SSE 回放流。

    Args:
        redis: 当前进程使用的异步 Redis 客户端。
        event: 进度、完成或失败事件模型。

    Returns:
        事件写入成功后无返回值。

    Raises:
        redis.exceptions.RedisError: Redis 写入或过期时间设置失败。
    """
    key = f"{EVENTS_PREFIX}{event.request_id}"
    await redis.xadd(key, {"payload": event.model_dump_json()})
    await redis.expire(key, EVENT_TTL_SECONDS)


async def process_message(redis: Redis, kind: str, fields: dict[str, str]) -> None:
    """处理一条工作消息，并发布下一阶段消息或终态事件。

    Args:
        redis: 当前进程使用的异步 Redis 客户端。
        kind: 工作进程角色，取值为 `sentiment` 或 `reply`。
        fields: Redis Stream 中的一条消息字段。

    Returns:
        成功时将数据交接到下一阶段或写入最终事件。

    Raises:
        Exception: 数据校验、Agent 或 Redis 操作失败。
    """
    raw = json.loads(fields["payload"])
    request_id = UUID(fields["request_id"])
    if kind == "sentiment":
        job = RedisChatJob.model_validate(raw)
        await publish_event(
            redis,
            ProgressEvent(
                event_type="progress",
                request_id=request_id,
                stage="sentiment_processing",
                message="正在识别情感…",
            ),
        )
        result = await identify_sentiment(job.request_id, job.text)
        await redis.xadd(
            SENTIMENTS,
            {"request_id": str(result.request_id), "payload": result.model_dump_json()},
        )
        return

    message = RedisSentimentResult.model_validate(raw)
    await publish_event(
        redis,
        ProgressEvent(
            event_type="progress",
            request_id=request_id,
            stage="reply_processing",
            message="正在生成回复…",
        ),
    )
    reply = await generate_reply(message)
    result = ChatResult(
        request_id=message.request_id,
        sentiment=message.sentiment,
        reason=message.reason,
        reply=reply,
    )
    await publish_event(
        redis,
        CompletedEvent(event_type="completed", request_id=request_id, result=result),
    )


async def consume(kind: str) -> None:
    """持续消费指定阶段的 Redis Stream 工作消息。

    Args:
        kind: 工作进程角色，只能为 `sentiment` 或 `reply`。

    Returns:
        正常情况下持续运行，直到进程被停止。

    Raises:
        ValueError: `kind` 不是支持的工作进程角色。
        redis.exceptions.RedisError: Redis 连接、读取或确认失败。
    """
    if kind not in {"sentiment", "reply"}:
        raise ValueError(f"不支持的工作进程类型：{kind}")
    redis = Redis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True
    )
    input_stream, group = (REQUESTS, "sentiment") if kind == "sentiment" else (SENTIMENTS, "reply")
    try:
        await ensure_group(redis, input_stream, group)
        while True:
            batches = await redis.xreadgroup(
                group, kind, {input_stream: ">"}, count=1, block=5000
            )
            for _, entries in batches:
                for message_id, fields in entries:
                    try:
                        await process_message(redis, kind, fields)
                    except Exception as error:
                        missing_key = error.args[0] if isinstance(error, KeyError) and error.args else "未知"
                        logger.warning(
                            "工作阶段失败：kind=%s error_type=%s missing_key=%s",
                            kind,
                            type(error).__name__,
                            missing_key if isinstance(error, KeyError) else "不适用",
                        )
                        request_id = fields.get("request_id")
                        if request_id:
                            await publish_event(
                                redis,
                                FailureEvent(
                                    event_type="failed",
                                    request_id=UUID(request_id),
                                    failed_stage=(
                                        "sentiment_processing"
                                        if kind == "sentiment"
                                        else "reply_processing"
                                    ),
                                    error_code="AGENT_STEP_FAILED",
                                    message="当前步骤处理失败，请稍后重试。",
                                ),
                            )
                    finally:
                        await redis.xack(input_stream, group, message_id)
    finally:
        await redis.aclose()


def main() -> None:
    """解析命令行参数并启动指定工作进程。

    Args:
        无；进程类型从命令行位置参数读取。

    Returns:
        消费循环正常停止后无返回值。

    Raises:
        SystemExit: 参数缺失或值无效。
    """
    parser = argparse.ArgumentParser(description="启动 OpenAI Agents 工作进程")
    parser.add_argument("kind", choices=("sentiment", "reply"))
    args = parser.parse_args()
    try:
        asyncio.run(consume(args.kind))
    except KeyboardInterrupt:
        print("收到 Ctrl+C，worker 已停止并关闭 Redis 连接。")


if __name__ == "__main__":
    main()
