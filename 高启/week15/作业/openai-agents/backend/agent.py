"""创建并运行 OpenAI Agents SDK 的两个业务 Agent。"""

import json
import os

from agents import Agent, AsyncOpenAI, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from pydantic import BaseModel, ConfigDict, Field

from backend.prompts import (
    REPLY_INPUT_TEMPLATE,
    REPLY_INSTRUCTIONS,
    SENTIMENT_INPUT_TEMPLATE,
    SENTIMENT_INSTRUCTIONS,
)


class SentimentOutput(BaseModel):
    """情感 Agent 的结构化输出。"""

    model_config = ConfigDict(extra="forbid")

    sentiment: str = Field(pattern="^(positive|negative|neutral)$")
    reason: str


def create_model(client: AsyncOpenAI) -> OpenAIChatCompletionsModel:
    """从 DeepSeek 环境配置创建 OpenAI 兼容聊天模型。

    Args:
        client: 已配置密钥和服务地址的 OpenAI SDK 客户端。

    Returns:
        Agents SDK 使用的 Chat Completions 模型。

    Raises:
        KeyError: 未配置 `DEEPSEEK_MODEL`。
    """
    return OpenAIChatCompletionsModel(
        model=os.environ["DEEPSEEK_MODEL"], openai_client=client
    )


def create_sentiment_agent(model: OpenAIChatCompletionsModel) -> Agent:
    """创建第 1 步情感识别 Agent。

    Args:
        model: 连接 DeepSeek 的聊天模型。

    Returns:
        使用情感识别提示词的 Agent。
    """
    return Agent(name="情感识别 Agent", instructions=SENTIMENT_INSTRUCTIONS, model=model)


def create_reply_agent(model: OpenAIChatCompletionsModel) -> Agent:
    """创建第 2 步情绪回应 Agent。

    Args:
        model: 连接 DeepSeek 的聊天模型。

    Returns:
        使用情绪支持提示词的 Agent。
    """
    return Agent(name="情绪回应 Agent", instructions=REPLY_INSTRUCTIONS, model=model)


async def run_agent(agent: Agent, text: str) -> str:
    """运行指定 Agent 并返回最终文本。

    Args:
        agent: 已绑定提示词和模型的 Agent。
        text: 本步骤接收的输入内容。

    Returns:
        Agent 最终输出的字符串。

    Raises:
        Exception: 模型调用或 Agent 执行失败。
    """
    set_tracing_disabled(True)
    result = await Runner.run(agent, text)
    return str(result.final_output)


def parse_sentiment_output(output: str) -> SentimentOutput:
    """解析并校验情感 Agent 返回的 JSON。

    Args:
        output: Agent 返回的 JSON 字符串。

    Returns:
        校验后的情感标签和判断原因。

    Raises:
        json.JSONDecodeError: 输出不是合法 JSON。
        ValidationError: JSON 字段或情感标签不符合约定。
    """
    return SentimentOutput.model_validate(json.loads(output))


async def classify(text: str) -> SentimentOutput:
    """运行情感识别 Agent 并校验结构化输出。

    Args:
        text: 用户提交的本轮文本。

    Returns:
        结构化的情感类别和判断原因。

    Raises:
        Exception: 模型调用失败或输出无法解析。
    """
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
    )
    try:
        model = create_model(client)
        agent = create_sentiment_agent(model)
        output = await run_agent(agent, SENTIMENT_INPUT_TEMPLATE.format(text=text))
        return parse_sentiment_output(output)
    finally:
        await client.close()


async def compose_reply(text: str, sentiment: str, reason: str) -> str:
    """运行回复 Agent，根据分类结果生成支持性回应。

    Args:
        text: 用户提交的本轮文本。
        sentiment: 情感识别结果。
        reason: 情感识别原因。

    Returns:
        面向用户的中文鼓励、共情建议或中性回应。

    Raises:
        Exception: 模型调用失败。
    """
    set_tracing_disabled(True)
    client = AsyncOpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"],
        base_url=os.environ["DEEPSEEK_BASE_URL"],
    )
    try:
        model = create_model(client)
        agent = create_reply_agent(model)
        prompt = REPLY_INPUT_TEMPLATE.format(
            text=text, sentiment=sentiment, reason=reason
        )
        return await run_agent(agent, prompt)
    finally:
        await client.close()
