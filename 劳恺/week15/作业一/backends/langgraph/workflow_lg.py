"""LangGraph 版工作流。

本文件集中了 LangGraph 实现 Agent 的全部代码，**对照文件**是
``backends/openai_agents/workflow_oa.py``（OpenAI Agents SDK 版）。
两个文件暴露同名同签名的入口函数：

- ``run_analyze(message)`` -> 情绪识别结果
- ``run_respond(message, emotion_result, history, retry_feedback)`` -> 情绪响应结果

因此两套框架的 ``agent1/main.py`` 与 ``agent2/main.py`` 几乎完全一致，
框架差异全部集中在这两个工作流文件里。

章节与 ``workflow_oa.py`` 的对应关系：

============  ==============================================================
章节          LangGraph 侧的体现
============  ==============================================================
§1 模型调用   ``ChatOpenAI`` + ``invoke_model()``
§2 Agent 1    ``EmotionState`` 状态 + generate/parse/repair/fallback 节点
§3 Agent 1 图 ``StateGraph`` 装配与条件边（重试是图内自环）
§4 Agent 2    ``ResponseState`` 状态 + route/generate_*/parse/fallback 节点
§5 Agent 2 图 ``StateGraph`` 装配与条件边（策略路由由条件边承担）
§6 对外入口   ``run_analyze()`` / ``run_respond()``
============  ==============================================================
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from backends.shared.constants.fallbacks import keyword_guess_emotion
from backends.shared.constants.strategies import ResponseStrategy
from backends.shared.errors import WorkflowError
from backends.shared.llm.client import get_llm_config
from backends.shared.llm.parsing import (
    LLMOutputError,
    assert_strategy_allowed,
    coerce_strategy,
    parse_emotion_result,
    parse_response_result,
)
from backends.shared.llm.prompts import (
    EMOTION_INSTRUCTIONS,
    RESPONSE_INSTRUCTIONS,
    STRATEGY_INSTRUCTIONS,
    build_emotion_prompt,
    build_response_prompt,
    strategy_hint,
)
from backends.shared.policy.strategy_router import choose_preferred_strategy
from backends.shared.quality.checker import build_fallback_response
from backends.shared.schemas.agent_api import HistoryMessage
from backends.shared.schemas.emotion import EmotionResult
from backends.shared.schemas.response import ResponseResult

logger = logging.getLogger(__name__)

#: 首次生成 + 最多一次修正重试（两个 Agent 共用）。
MAX_ATTEMPTS = 2


# ===========================================================================
# §1 模型调用
# ===========================================================================


@lru_cache(maxsize=1)
def get_chat_model() -> ChatOpenAI:
    """构造（并缓存）ChatOpenAI 客户端。

    延迟到第一次真正调用时才构造，这样导入模块不会因为缺少 API Key 而失败，
    健康检查依然可以返回结果。
    """
    config = get_llm_config()
    kwargs: dict = {
        "model": config.model,
        "api_key": config.api_key,
        "temperature": config.temperature,
        "max_tokens": config.max_output_tokens,
    }
    if config.base_url:
        kwargs["base_url"] = config.base_url
    return ChatOpenAI(**kwargs)


def extract_text(content: object) -> str:
    """把 LangChain 的消息内容统一成纯文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return "" if content is None else str(content)


async def invoke_model(system_prompt: str, user_prompt: str) -> str:
    """调用模型并返回文本输出。

    对应 Agents SDK 版的 ``Runner.run()``：都是"把两段提示词交给模型、拿回一段文本"，
    区别在于 LangGraph 走 LangChain 的 ``ainvoke``，Agents SDK 走 SDK 的 Runner。
    """
    try:
        model = get_chat_model()
        response = await model.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
    except Exception as exc:  # 模型不可用（鉴权、限流、网络…）统一转成 WorkflowError
        raise WorkflowError(f"调用模型失败: {exc}") from exc
    return extract_text(response.content)


# ===========================================================================
# §2 Agent 1：情绪识别（状态 + 节点）
# ===========================================================================


class EmotionState(TypedDict, total=False):
    """Agent 1 在节点之间传递的状态。

    全部字段都是可选的（``total=False``），节点只返回自己更新的部分。
    """

    message: str
    prompt: str
    raw_output: str
    result: EmotionResult | None
    attempts: int
    error: str | None
    used_fallback: bool


async def emotion_generate_node(state: EmotionState) -> dict[str, Any]:
    """调用模型生成情绪识别结果。"""
    prompt = build_emotion_prompt(state["message"])
    raw_output = await invoke_model(EMOTION_INSTRUCTIONS, prompt)
    return {"prompt": prompt, "raw_output": raw_output, "error": None}


async def emotion_parse_node(state: EmotionState) -> dict[str, Any]:
    """解析模型输出。"""
    attempts = int(state.get("attempts", 0)) + 1
    try:
        result = parse_emotion_result(state.get("raw_output", ""))
    except LLMOutputError as exc:
        logger.warning("情绪识别输出解析失败（第 %s 次）：%s", attempts, exc)
        return {"result": None, "attempts": attempts, "error": str(exc)}
    return {"result": result, "attempts": attempts, "error": None}


async def emotion_repair_node(state: EmotionState) -> dict[str, Any]:
    """带着纠正要求重新生成一次。"""
    feedback = f"上一次输出无法解析为规定格式：{state.get('error')}。"
    prompt = build_emotion_prompt(state["message"], retry_feedback=feedback)
    raw_output = await invoke_model(EMOTION_INSTRUCTIONS, prompt)
    return {"prompt": prompt, "raw_output": raw_output}


def emotion_fallback_node(state: EmotionState) -> dict[str, Any]:
    """模型始终不可用时按关键词兜底。"""
    category = keyword_guess_emotion(state["message"])
    result = EmotionResult(
        emotion=category,
        confidence=0.3,
        emotion_type="other",
        reason="模型输出无法解析，已按关键词兜底判断",
    )
    logger.warning("情绪识别走兜底分支：%s", result.model_dump())
    return {"result": result, "used_fallback": True}


def emotion_route_after_parse(state: EmotionState) -> str:
    """解析后的条件边：成功 -> END，失败 -> repair / fallback。"""
    if state.get("result") is not None:
        return END
    if int(state.get("attempts", 0)) < MAX_ATTEMPTS:
        return "repair"
    return "fallback"


# ===========================================================================
# §3 Agent 1 图装配
# ===========================================================================
#
#   START -> generate -> parse ->(解析成功)--> END
#                              |
#                              +--(解析失败且未超次数)--> repair -> parse
#                              |
#                              +--(超出次数)--> fallback -> END


@lru_cache(maxsize=1)
def get_emotion_graph():
    """构造并缓存情绪识别图。"""
    builder = StateGraph(EmotionState)
    builder.add_node("generate", emotion_generate_node)
    builder.add_node("parse", emotion_parse_node)
    builder.add_node("repair", emotion_repair_node)
    builder.add_node("fallback", emotion_fallback_node)

    builder.add_edge(START, "generate")
    builder.add_edge("generate", "parse")
    builder.add_conditional_edges(
        "parse",
        emotion_route_after_parse,
        {"repair": "repair", "fallback": "fallback", END: END},
    )
    builder.add_edge("repair", "parse")
    builder.add_edge("fallback", END)

    return builder.compile()


# ===========================================================================
# §4 Agent 2：情绪响应（状态 + 节点）
# ===========================================================================


class ResponseState(TypedDict, total=False):
    """Agent 2 在节点之间传递的状态。"""

    message: str
    emotion_result: EmotionResult
    history: list[dict[str, str]]
    retry_feedback: str | None
    preferred_strategy: str
    prompt: str
    raw_output: str
    result: ResponseResult | None
    attempts: int
    error: str | None
    used_fallback: bool


#: 三个生成分支的节点名。
GENERATE_NODES: dict[str, str] = {
    strategy.value: f"generate_{strategy.value}" for strategy in ResponseStrategy
}


def response_route_node(state: ResponseState) -> dict[str, Any]:
    """根据情绪与危机信号选出首选策略。"""
    preferred = choose_preferred_strategy(state["emotion_result"], state["message"])
    return {"preferred_strategy": preferred.value}


def response_route_by_strategy(state: ResponseState) -> str:
    """条件边：根据首选策略选择生成分支的节点名。"""
    preferred = state.get("preferred_strategy") or ResponseStrategy.EMPATHY.value
    return GENERATE_NODES.get(preferred, GENERATE_NODES[ResponseStrategy.EMPATHY.value])


def _combined_feedback(state: ResponseState) -> str | None:
    """合并编排器给出的质量反馈与图内部的解析错误。"""
    parts: list[str] = []
    external = state.get("retry_feedback")
    if external:
        parts.append(external)
    error = state.get("error")
    if error:
        parts.append(f"上一次输出无法解析为规定格式：{error}。")
    return "\n".join(parts) if parts else None


def _make_generate_node(strategy: ResponseStrategy):
    """生成某个策略分支的节点函数。"""

    async def node(state: ResponseState) -> dict[str, Any]:
        history = [HistoryMessage.model_validate(row) for row in state.get("history", [])]
        emotion = state["emotion_result"]
        prompt = build_response_prompt(
            state["message"], emotion, history, _combined_feedback(state)
        )
        system_prompt = (
            f"{RESPONSE_INSTRUCTIONS}\n{strategy_hint(emotion)}\n"
            f"{STRATEGY_INSTRUCTIONS[strategy.value]}"
        )
        raw_output = await invoke_model(system_prompt, prompt)
        return {"prompt": prompt, "raw_output": raw_output}

    node.__name__ = f"generate_{strategy.value}"
    return node


async def response_parse_node(state: ResponseState) -> dict[str, Any]:
    """解析模型输出，并校验策略与情绪是否匹配。"""
    attempts = int(state.get("attempts", 0)) + 1
    default = coerce_strategy(state.get("preferred_strategy")) or ResponseStrategy.EMPATHY
    try:
        result = parse_response_result(state.get("raw_output", ""), default)
        assert_strategy_allowed(result, state["emotion_result"])
    except LLMOutputError as exc:
        logger.warning("情绪响应输出解析失败（第 %s 次）：%s", attempts, exc)
        return {"result": None, "attempts": attempts, "error": str(exc)}
    return {"result": result, "attempts": attempts, "error": None}


def response_route_after_parse(state: ResponseState) -> str:
    """解析后的条件边：成功 -> END，失败 -> 重试生成 / 兜底。"""
    if state.get("result") is not None:
        return END
    if int(state.get("attempts", 0)) < MAX_ATTEMPTS:
        return response_route_by_strategy(state)
    return "fallback"


def response_fallback_node(state: ResponseState) -> dict[str, Any]:
    """模型始终不可用时返回兜底回复。"""
    result = build_fallback_response(state["emotion_result"])
    logger.warning("情绪响应走兜底分支：%s", result.model_dump())
    return {"result": result, "used_fallback": True}


# ===========================================================================
# §5 Agent 2 图装配
# ===========================================================================
#
#   START -> route --(条件边: 策略)--> generate_{encouragement|empathy|support}
#                                               |
#                                               v
#                                             parse --(解析成功)--> END
#                                               |
#                       (失败且未超次数) -----+--> generate_* （重试）
#                                               |
#                       (超出次数) -------------+--> fallback -> END


@lru_cache(maxsize=1)
def get_response_graph():
    """构造并缓存情绪响应图。"""
    builder = StateGraph(ResponseState)
    builder.add_node("route", response_route_node)
    for strategy in ResponseStrategy:
        builder.add_node(GENERATE_NODES[strategy.value], _make_generate_node(strategy))
    builder.add_node("parse", response_parse_node)
    builder.add_node("fallback", response_fallback_node)

    builder.add_edge(START, "route")
    builder.add_conditional_edges(
        "route",
        response_route_by_strategy,
        {node_name: node_name for node_name in GENERATE_NODES.values()},
    )
    for node_name in GENERATE_NODES.values():
        builder.add_edge(node_name, "parse")

    parse_paths = {node_name: node_name for node_name in GENERATE_NODES.values()}
    parse_paths.update({"fallback": "fallback", END: END})
    builder.add_conditional_edges("parse", response_route_after_parse, parse_paths)
    builder.add_edge("fallback", END)

    return builder.compile()


# ===========================================================================
# §6 对外入口（与 workflow_oa.py 同名同签名）
# ===========================================================================


async def run_analyze(message: str) -> EmotionResult:
    """运行情绪识别工作流。

    重试与兜底都在图里完成：解析失败走 ``repair`` 节点，节点用尽走 ``fallback`` 节点。
    """
    graph = get_emotion_graph()
    state = await graph.ainvoke({"message": message})
    result = state.get("result")
    if result is None:  # pragma: no cover - 图的兜底节点一定会给出结果
        raise WorkflowError("情绪识别失败")
    return result


async def run_respond(
    message: str,
    emotion_result: EmotionResult,
    history: list[HistoryMessage],
    retry_feedback: str | None = None,
) -> ResponseResult:
    """运行情绪响应工作流。"""
    graph = get_response_graph()
    state = await graph.ainvoke(
        {
            "message": message,
            "emotion_result": emotion_result,
            "history": [row.model_dump() for row in history],
            "retry_feedback": retry_feedback,
        }
    )
    result = state.get("result")
    if result is None:  # pragma: no cover - 图的兜底节点一定会给出结果
        raise WorkflowError("情绪响应生成失败")
    return result
