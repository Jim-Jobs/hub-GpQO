"""模型输出解析。

两套框架共用同一套解析逻辑，保证 ``/analyze`` 与 ``/respond`` 的输出结构
完全一致。解析失败时抛出 :class:`LLMOutputError`，由 Agent 触发一次修正重试，
再失败则走兜底逻辑。
"""

from __future__ import annotations

import json
import re
from typing import Any

from backends.shared.constants.emotions import OTHER_EMOTION_TYPE, EmotionCategory
from backends.shared.constants.strategies import (
    ALLOWED_STRATEGIES_BY_CATEGORY,
    ResponseStrategy,
)
from backends.shared.schemas.emotion import EmotionResult
from backends.shared.schemas.response import ResponseResult


class LLMOutputError(ValueError):
    """模型输出无法解析成目标结构。"""


_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*")


def _strip_code_fences(text: str) -> str:
    """去掉 Markdown 代码块标记，但保留其中的内容。"""
    return _CODE_FENCE_RE.sub("", text).replace("```", "")


def extract_json_object(text: str) -> dict[str, Any]:
    """从模型输出中提取第一个完整的 JSON 对象。

    支持模型在 JSON 前后附带说明文字、或用 ```json 代码块包裹的情况。
    """
    if not text or not text.strip():
        raise LLMOutputError("模型输出为空")

    cleaned = _strip_code_fences(text)
    start = cleaned.find("{")
    if start == -1:
        raise LLMOutputError("模型输出中未找到 JSON 对象")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                chunk = cleaned[start : index + 1]
                try:
                    data = json.loads(chunk)
                except json.JSONDecodeError as exc:
                    raise LLMOutputError(f"JSON 解析失败: {exc}") from exc
                if not isinstance(data, dict):
                    raise LLMOutputError(f"JSON 顶层不是对象，而是 {type(data).__name__}")
                return data
    raise LLMOutputError("模型输出中的 JSON 对象不完整")


def _pick(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """按候选键名依次取值，兼容模型对字段名的不同写法。"""
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return default


def _coerce_float(value: Any, default: float = 0.5) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1", "是", "一致"}:
            return True
        if lowered in {"false", "no", "0", "否", "不一致"}:
            return False
    return default


_STRATEGY_ALIASES: dict[str, ResponseStrategy] = {
    "encouragement": ResponseStrategy.ENCOURAGEMENT,
    "encourage": ResponseStrategy.ENCOURAGEMENT,
    "encouraging": ResponseStrategy.ENCOURAGEMENT,
    "positive": ResponseStrategy.ENCOURAGEMENT,
    "empathy": ResponseStrategy.EMPATHY,
    "empathetic": ResponseStrategy.EMPATHY,
    "emotional_support": ResponseStrategy.EMPATHY,
    "support": ResponseStrategy.SUPPORT,
    "supportive": ResponseStrategy.SUPPORT,
    "safety": ResponseStrategy.SUPPORT,
    "安全": ResponseStrategy.SUPPORT,
    "共情": ResponseStrategy.EMPATHY,
    "鼓励": ResponseStrategy.ENCOURAGEMENT,
}


def coerce_strategy(value: Any) -> ResponseStrategy | None:
    """把模型输出的策略名归一化，无法识别时返回 ``None``。"""
    if isinstance(value, ResponseStrategy):
        return value
    if not isinstance(value, str):
        return None
    cleaned = value.strip().lower()
    if not cleaned:
        return None
    if cleaned in _STRATEGY_ALIASES:
        return _STRATEGY_ALIASES[cleaned]
    if "encourag" in cleaned or "鼓励" in cleaned or "庆祝" in cleaned:
        return ResponseStrategy.ENCOURAGEMENT
    if "empath" in cleaned or "共情" in cleaned:
        return ResponseStrategy.EMPATHY
    if "support" in cleaned or "支持" in cleaned:
        return ResponseStrategy.SUPPORT
    return None


def parse_emotion_result(raw: str) -> EmotionResult:
    """把模型输出解析成 :class:`EmotionResult`。"""
    payload = extract_json_object(raw)

    emotion_raw = _pick(payload, "emotion", "emotion_category", "polarity", "sentiment")
    emotion = str(emotion_raw or "").strip().lower()
    if emotion not in {category.value for category in EmotionCategory}:
        raise LLMOutputError(f"emotion 取值非法: {emotion_raw!r}")

    confidence = _coerce_float(_pick(payload, "confidence", "score", "certainty"), default=0.5)
    confidence = min(max(confidence, 0.0), 1.0)

    emotion_type = _pick(payload, "emotion_type", "emotionType", "specific_emotion", "type")
    reason = _pick(payload, "reason", "explanation", "analysis", "reasoning")

    result = EmotionResult(
        emotion=emotion,
        confidence=confidence,
        emotion_type=str(emotion_type or OTHER_EMOTION_TYPE),
        reason=str(reason or ""),
    )
    if not result.is_consistent():
        result = result.demote_to_other("（具体情绪与极性不一致，已降级为 other）")
    return result


def parse_response_result(
    raw: str, default_strategy: ResponseStrategy
) -> ResponseResult:
    """把模型输出解析成 :class:`ResponseResult`。"""
    payload = extract_json_object(raw)

    strategy_raw = _pick(payload, "strategy", "response_strategy", "type")
    strategy = coerce_strategy(strategy_raw) or default_strategy

    reply_raw = _pick(payload, "reply", "response", "message", "content", "text", default="")
    reply = str(reply_raw or "").strip()

    is_consistent = _coerce_bool(
        _pick(payload, "is_consistent", "consistent", "isConsistent"), default=True
    )

    return ResponseResult(strategy=strategy, reply=reply, is_consistent=is_consistent)


def assert_strategy_allowed(
    result: ResponseResult, emotion: EmotionResult
) -> None:
    """策略必须与情绪极性匹配，否则抛出 :class:`LLMOutputError`。

    Agent 自身在返回前调用它，可以在把结果交给编排器之前先修正一次，
    避免质量检查失败导致的往返。
    """
    allowed = ALLOWED_STRATEGIES_BY_CATEGORY.get(EmotionCategory(emotion.emotion), set())
    if result.strategy not in allowed:
        allowed_names = "、".join(sorted(item.value for item in allowed))
        raise LLMOutputError(
            f"策略 {result.strategy.value} 与情绪 {emotion.emotion} 不匹配，应使用 {allowed_names}"
        )
