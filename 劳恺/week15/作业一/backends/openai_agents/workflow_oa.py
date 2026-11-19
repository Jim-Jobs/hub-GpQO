"""OpenAI Agents SDK 版工作流。

本文件集中了 Agents SDK 实现 Agent 的全部代码，**对照文件**是
``backends/langgraph/workflow_lg.py``（LangGraph 版）。
两个文件暴露同名同签名的入口函数：

- ``run_analyze(message)`` -> 情绪识别结果
- ``run_respond(message, emotion_result, history, retry_feedback)`` -> 情绪响应结果

因此两套框架的 ``agent1/main.py`` 与 ``agent2/main.py`` 几乎完全一致，
框架差异全部集中在这两个工作流文件里。

章节与 ``workflow_lg.py`` 的对应关系：

============  ==============================================================
章节          Agents SDK 侧的体现
============  ==============================================================
§1 模型调用   ``OpenAIChatCompletionsModel`` + ``Runner.run()`` + ``ModelSettings``
§2 Agent 1    ``Agent`` 类定义（instructions 即系统提示词）
§3 Agent 1 图 状态由 SDK 托管；重试是 ``run_analyze`` 里的循环
§4 Agent 2    三个专员 ``Agent`` + ``ResponseRouter``
§5 Agent 2 图 策略路由由 **handoff（移交）** 承担
§6 对外入口   ``run_analyze()`` / ``run_respond()``
============  ==============================================================
"""

from __future__ import annotations

import logging
from functools import lru_cache

from agents import Agent, ModelSettings, Runner, set_tracing_disabled
from agents.exceptions import AgentsException
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from backends.shared.constants.fallbacks import keyword_guess_emotion
from backends.shared.constants.strategies import ResponseStrategy
from backends.shared.errors import WorkflowError
from backends.shared.llm.client import get_llm_config
from backends.shared.llm.parsing import (
    LLMOutputError,
    assert_strategy_allowed,
    parse_emotion_result,
    parse_response_result,
)
from backends.shared.llm.prompts import (
    EMOTION_INSTRUCTIONS,
    RESPONSE_INSTRUCTIONS,
    STRATEGY_INSTRUCTIONS,
    build_emotion_prompt,
    build_response_prompt,
)
from backends.shared.policy.strategy_router import choose_preferred_strategy
from backends.shared.quality.checker import build_fallback_response
from backends.shared.schemas.agent_api import HistoryMessage
from backends.shared.schemas.emotion import EmotionResult
from backends.shared.schemas.response import ResponseResult

logger = logging.getLogger(__name__)

# 本地或自建服务下 SDK 的追踪上报会失败并刷日志，直接关闭。
set_tracing_disabled(True)

#: 单次 Runner 允许的最大轮次（Agent 2 的路由 + 移交需要几轮）。
MAX_TURNS = 8

#: 首次生成 + 最多一次修正重试（两个 Agent 共用）。
MAX_ATTEMPTS = 2


# ===========================================================================
# §1 模型调用
# ===========================================================================


@lru_cache(maxsize=1)
def get_agents_model() -> str | OpenAIChatCompletionsModel:
    """构造 SDK 使用的模型。

    指定了 ``OPENAI_BASE_URL`` 时使用自定义的 OpenAI 兼容端点，
    否则交给 SDK 使用默认客户端。

    对应 LangGraph 版的 ``ChatOpenAI``：两者读取同一份 ``.env`` 配置，
    因此模型、温度与最大输出长度完全一致。
    """
    config = get_llm_config()
    if config.base_url:
        client = AsyncOpenAI(api_key=config.api_key, base_url=config.base_url)
        return OpenAIChatCompletionsModel(model=config.model, openai_client=client)
    return config.model


@lru_cache(maxsize=1)
def get_model_settings() -> ModelSettings:
    """生成参数设置。"""
    config = get_llm_config()
    return ModelSettings(
        temperature=config.temperature, max_tokens=config.max_output_tokens
    )


def final_text(result) -> str:
    """把 SDK 的运行结果统一成纯文本。"""
    output = getattr(result, "final_output", None)
    if isinstance(output, str):
        return output
    if output is None:
        return ""
    return str(output)


async def run_agent(agent: Agent, prompt: str) -> str:
    """运行一个 Agent 并取回文本输出。

    对应 LangGraph 版的 ``invoke_model()``：区别在于这里由 SDK 的 Runner
    负责多轮对话与工具调用，LangGraph 侧则由图里的节点负责。
    """
    try:
        result = await Runner.run(agent, prompt, max_turns=MAX_TURNS)
    except AgentsException as exc:
        raise WorkflowError(f"调用模型失败: {exc}") from exc
    return final_text(result)


# ===========================================================================
# §2 Agent 1：情绪识别（Agent 定义）
# ===========================================================================

EMOTION_AGENT_NAME = "EmotionAgent"


@lru_cache(maxsize=1)
def get_emotion_agent() -> Agent:
    """构造（并缓存）情绪识别 Agent。

    LangGraph 版把这段提示词放进系统消息、显式声明状态与节点；
    Agents SDK 版只需要一个 ``Agent``：instructions 即系统提示词，
    状态与循环由 SDK 托管。
    """
    return Agent(
        name=EMOTION_AGENT_NAME,
        instructions=EMOTION_INSTRUCTIONS,
        model=get_agents_model(),
        model_settings=get_model_settings(),
    )


# ===========================================================================
# §3 Agent 1 图（状态由 SDK 托管，重试是循环）
# ===========================================================================


async def run_analyze(message: str) -> EmotionResult:
    """运行情绪识别工作流。

    对应 LangGraph 版的 ``repair`` 自环：这里用一次循环重跑完成同样的语义。
    """
    agent = get_emotion_agent()
    prompt = build_emotion_prompt(message)
    current_prompt = prompt
    last_error: LLMOutputError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        text = await run_agent(agent, current_prompt)
        try:
            return parse_emotion_result(text)
        except LLMOutputError as exc:
            last_error = exc
            logger.warning("情绪识别输出解析失败（第 %s 次）：%s", attempt, exc)
            current_prompt = (
                f"{prompt}\n\n上一次输出无法解析为规定格式：{exc}。"
                "请严格按照规则重新输出，只输出 JSON 对象。"
            )

    # 始终无法解析：按关键词兜底，保证 Agent 永远返回合法结构。
    category = keyword_guess_emotion(message)
    logger.warning("情绪识别走兜底分支：%s", last_error)
    return EmotionResult(
        emotion=category,
        confidence=0.3,
        emotion_type="other",
        reason="模型输出无法解析，已按关键词兜底判断",
    )


# ===========================================================================
# §4 Agent 2：情绪响应（专员 Agent + 路由器）
# ===========================================================================

ROUTER_AGENT_NAME = "ResponseRouter"

ROUTER_INSTRUCTIONS = """你是情绪响应路由 Agent。你的任务是判断该由哪个专员 Agent 来回复用户。

移交规则：
- 情绪为 positive 时，移交给 EncouragementAgent（鼓励与庆祝）。
- 情绪为 negative 且没有出现危机信号时，移交给 EmpathyAgent（共情接纳）。
- 情绪为 negative 且出现危机信号（自杀、自残、不想活等）时，移交给 SupportAgent（安全支持）。

请只做移交，不要自己生成回复内容，最终回复由专员 Agent 给出。"""

HANDOFF_DESCRIPTIONS: dict[str, str] = {
    ResponseStrategy.ENCOURAGEMENT.value: (
        "负责正向情绪的回复：鼓励、庆祝、肯定用户的积极进展。"
    ),
    ResponseStrategy.EMPATHY.value: (
        "负责一般负向情绪的回复：共情与接纳，让用户感到被理解。"
    ),
    ResponseStrategy.SUPPORT.value: (
        "负责出现危机信号的回复：安全支持，温和地引导用户寻求现实中的帮助。"
    ),
}


def _build_specialist(strategy: ResponseStrategy) -> Agent:
    """构造某个策略的专员 Agent。"""
    return Agent(
        name=f"{strategy.value.capitalize()}Agent",
        handoff_description=HANDOFF_DESCRIPTIONS[strategy.value],
        instructions=(
            f"{RESPONSE_INSTRUCTIONS}\n"
            f"{STRATEGY_INSTRUCTIONS[strategy.value]}\n"
            f'本次 strategy 必须取 "{strategy.value}"。'
        ),
        model=get_agents_model(),
        model_settings=get_model_settings(),
    )


@lru_cache(maxsize=1)
def get_response_agents() -> tuple[Agent, dict[str, Agent]]:
    """返回 (路由器 Agent, 各策略对应的专员 Agent)。

    LangGraph 版用**条件边**把任务路由到三个生成分支；这里用 SDK 的
    **handoff（移交）**：路由器把控制权移交给专员 Agent，由专员产出最终回复。
    """
    specialists = {
        strategy.value: _build_specialist(strategy) for strategy in ResponseStrategy
    }
    router = Agent(
        name=ROUTER_AGENT_NAME,
        instructions=ROUTER_INSTRUCTIONS,
        handoffs=list(specialists.values()),
        model=get_agents_model(),
        model_settings=get_model_settings(),
    )
    return router, specialists


# ===========================================================================
# §5 Agent 2 图（策略路由由 handoff 承担）
# ===========================================================================


def _parse_or_raise(
    text: str, emotion: EmotionResult, preferred: ResponseStrategy
) -> ResponseResult:
    result = parse_response_result(text, preferred)
    assert_strategy_allowed(result, emotion)
    return result


async def run_respond(
    message: str,
    emotion_result: EmotionResult,
    history: list[HistoryMessage],
    retry_feedback: str | None = None,
) -> ResponseResult:
    """运行情绪响应工作流。"""
    preferred = choose_preferred_strategy(emotion_result, message)
    prompt = build_response_prompt(message, emotion_result, history, retry_feedback)
    router, specialists = get_response_agents()

    # 第一次：让路由器按情绪选择专员 Agent 并生成回复。
    text = await run_agent(router, prompt)
    try:
        return _parse_or_raise(text, emotion_result, preferred)
    except LLMOutputError as exc:
        logger.warning("路由器产出的回复不可用：%s", exc)

    # 第二次：直接调用确定性选出的专员 Agent 重试一次。
    specialist = specialists[preferred.value]
    corrective_prompt = (
        f"{prompt}\n\n上一次输出未通过检查，请严格按照规则重新输出，只输出 JSON 对象。"
    )
    try:
        text = await run_agent(specialist, corrective_prompt)
        return _parse_or_raise(text, emotion_result, preferred)
    except LLMOutputError as exc:
        logger.warning("专员 Agent 重试仍失败，使用兜底回复：%s", exc)
        return build_fallback_response(emotion_result)


# ===========================================================================
# §6 对外入口（与 workflow_lg.py 同名同签名）
# ===========================================================================
