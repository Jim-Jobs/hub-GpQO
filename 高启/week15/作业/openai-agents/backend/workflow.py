"""将 Agent 输出转换为跨进程通用业务消息。"""

from uuid import UUID

from backend.agent import classify, compose_reply
from backend.schemas import RedisSentimentResult, Sentiment


async def identify_sentiment(request_id: UUID, text: str) -> RedisSentimentResult:
    """执行识别阶段并生成回复进程可消费的消息。

    Args:
        request_id: 本轮工作流编号。
        text: 用户本轮输入。

    Returns:
        可写入情感结果 Redis Stream 的消息。

    Raises:
        Exception: Agent 调用或结果校验失败。
    """
    result = await classify(text)
    return RedisSentimentResult(
        request_id=request_id,
        text=text,
        sentiment=Sentiment(result.sentiment),
        reason=result.reason,
    )


async def generate_reply(message: RedisSentimentResult) -> str:
    """执行回复阶段并返回面向用户的回复正文。

    Args:
        message: 情感识别进程产出的标准消息。

    Returns:
        回复 Agent 生成的文本。

    Raises:
        Exception: Agent 调用失败。
    """
    return await compose_reply(message.text, message.sentiment.value, message.reason)
