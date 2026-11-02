"""LLM 客户端工厂：OpenAI 兼容接口（默认智谱 GLM）。

通过环境变量配置，代码不感知具体供应商：
    LLM_BASE_URL / LLM_API_KEY / LLM_MODEL / LLM_FAST_MODEL / LLM_THINKING / LLM_BACKEND

两个后端：
    native   （默认）sql_agent.llm_client.ChatClient —— httpx 直连，冷启动快 ~2s
    langchain         langchain_openai.ChatOpenAI —— 需要生态集成时切换

性能要点：智谱 GLM-4.5 系列默认开启深度思考（单次调用 10~20s）。
本项目在生成 SQL 这类结构化短输出任务上关闭思维链，实测质量不降而延迟降 3~12 倍。
"""

from __future__ import annotations

from typing import Any

from sql_agent import config
from sql_agent.llm_client import ChatClient
from sql_agent.log import get_logger

logger = get_logger(__name__)

# 支持 thinking 参数的推理模型前缀（仅这些模型注入，其他供应商/模型不受影响）
_REASONING_MODEL_PREFIXES = ("glm-4.5", "glm-4.6", "glm-5", "glm-z1")


def is_reasoning_model(model: str) -> bool:
    """判断是否支持 thinking 参数的推理模型。"""
    return model.lower().startswith(_REASONING_MODEL_PREFIXES)


def resolve_extra_body(model: str) -> dict:
    """按配置与模型能力解析请求附加参数（思维链开关）。

    - LLM_THINKING=disabled → 显式关闭思考（最快，推荐结构化任务）
    - LLM_THINKING=enabled  → 显式开启（复杂推理任务）
    - LLM_THINKING=auto     → 推理模型默认关闭，其余不注入
    - 非推理模型一律不注入，避免不支持的供应商返回 400
    """
    mode = (config.LLM_THINKING or "auto").lower()
    if mode == "auto":
        mode = "disabled" if is_reasoning_model(model) else ""
    if mode not in ("enabled", "disabled") or not is_reasoning_model(model):
        return {}
    return {"thinking": {"type": mode}}


def get_llm(temperature: float = 0.0, model: str | None = None,
            max_tokens: int | None = None) -> Any:
    """创建 LLM 客户端（确定性优先，temperature 默认 0）。

    参数：
        temperature: 采样温度，默认 0（SQL 场景需要确定性）
        model:       覆盖模型名，缺省用 config.LLM_MODEL
        max_tokens:  输出上限，可压缩长回答的生成耗时
    """
    if not config.LLM_API_KEY:
        raise ValueError(
            "未配置 LLM_API_KEY：请复制 .env.example 为 .env 并填入 API key"
        )
    model = model or config.LLM_MODEL
    extra_body = resolve_extra_body(model)

    if config.LLM_BACKEND == "langchain":
        from langchain_openai import ChatOpenAI  # 延迟导入，仅该后端需要

        kwargs: dict = {
            "base_url": config.LLM_BASE_URL,
            "api_key": config.LLM_API_KEY,
            "model": model,
            "temperature": temperature,
            "timeout": config.REQUEST_TIMEOUT,
            "max_retries": config.LLM_MAX_RETRIES,
        }
        if extra_body:
            kwargs["extra_body"] = extra_body
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        logger.debug("get_llm: backend=langchain, model=%s", model)
        return ChatOpenAI(**kwargs)

    logger.debug("get_llm: backend=native, model=%s, extra_body=%s",
                 model, extra_body or "-")
    return ChatClient(
        base_url=config.LLM_BASE_URL,
        api_key=config.LLM_API_KEY,
        model=model,
        temperature=temperature,
        timeout=config.REQUEST_TIMEOUT,
        max_retries=config.LLM_MAX_RETRIES,
        extra_body=extra_body,
        max_tokens=max_tokens,
    )


def get_fast_llm(temperature: float = 0.0) -> Any:
    """创建辅助节点用 LLM（改写/评审/组织回答）。

    config.LLM_FAST_MODEL 为空时与主模型一致；配置更轻的模型可进一步降延迟。
    """
    return get_llm(temperature=temperature,
                   model=config.LLM_FAST_MODEL or config.LLM_MODEL)


def describe_llm() -> str:
    """返回当前 LLM 配置摘要（用于启动日志/性能报告）。"""
    main_model = config.LLM_MODEL
    fast_model = config.LLM_FAST_MODEL or main_model
    thinking = resolve_extra_body(main_model).get("thinking", {}).get("type", "默认")
    return (f"后端={config.LLM_BACKEND}, 主模型={main_model}（thinking={thinking}）, "
            f"辅助模型={fast_model}")
