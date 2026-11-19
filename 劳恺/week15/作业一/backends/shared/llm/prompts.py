"""提示词模板。

两套 Agent 框架使用**完全相同**的提示词，保证两边的业务逻辑与输出结构一致，
这样框架之间的对比才只剩下编排机制本身的差异。

提示词要求模型输出裸 JSON（不带 Markdown 代码块），由
:mod:`shared.llm.parsing` 统一解析。这样做而不是直接使用各框架的
结构化输出特性，是为了兼容所有 OpenAI 兼容服务。
"""

from __future__ import annotations

from backends.shared.constants.emotions import EMOTION_TYPES
from backends.shared.schemas.agent_api import HistoryMessage
from backends.shared.schemas.emotion import EmotionResult

EMOTION_TYPE_LIST = " / ".join(EMOTION_TYPES)

EMOTION_INSTRUCTIONS = f"""你是情绪识别 Agent，负责判断用户最新一条消息的情绪倾向。

输出要求：
1. 只输出一个 JSON 对象，不要输出任何解释文字，不要使用 Markdown 代码块。
2. JSON 字段：
   - "emotion"：只能是 "positive" 或 "negative"。
   - "confidence"：0 到 1 之间的小数，表示判断的把握程度。
   - "emotion_type"：具体情绪，只能取 {EMOTION_TYPE_LIST} 之一。
   - "reason"：一句话说明判断依据（中文，40 字以内）。
3. 如果情绪不明显，"emotion_type" 填 "other"，并相应降低 "confidence"。
4. 只判断用户**最新一条**消息的情绪，不要被历史对话中的旧情绪带偏。

输出示例：
{{"emotion": "negative", "confidence": 0.92, "emotion_type": "anxiety",
 "reason": "用户表达了明显的工作压力"}}
"""

RESPONSE_INSTRUCTIONS = """你是情绪响应 Agent，负责根据识别出的情绪生成一条中文回复。

输出要求：
1. 只输出一个 JSON 对象，不要输出任何解释文字，不要使用 Markdown 代码块。
2. JSON 字段：
   - "strategy"：响应策略，只能取 "encouragement"、"empathy"、"support" 之一。
   - "reply"：回复正文，中文，1 到 3 句，120 字以内。
   - "is_consistent"：布尔值，表示回复是否与识别到的情绪相符。
3. 策略选择规则：
   - 情绪为 positive 时用 "encouragement"（鼓励、庆祝）或 "empathy"（共情）。
   - 情绪为 negative 时用 "empathy"（共情接纳）或 "support"（安全支持，必要时引导求助）。
   - 绝不要对 negative 情绪使用 "encouragement"。
4. 回复要求：先承接情绪再给回应；不要复述用户原话；不要提及"情绪识别""置信度""JSON"
   等内部术语；不要输出免责声明式的套话。

输出示例：
{"strategy": "empathy", "reply": "听起来你最近承受了不少压力，愿意的话可以多说说。",
 "is_consistent": true}
"""

REPAIR_INSTRUCTION = (
    "上一次输出不符合格式或内容要求，请严格按照上述规则重新输出，只输出 JSON 对象。"
)

#: 各响应策略的语气要求。两套框架使用完全相同的措辞。
STRATEGY_INSTRUCTIONS: dict[str, str] = {
    "encouragement": (
        "本次回复使用鼓励与庆祝的语气：肯定用户已经做到的事，具体地强化这份正向感受。"
    ),
    "empathy": (
        "本次回复使用共情接纳的语气：先准确承接用户的情绪，让用户感到被理解，再适度回应。"
    ),
    "support": (
        "本次回复使用安全支持的语气：先表达关心，再温和地鼓励用户寻求现实中的帮助"
        "（家人、朋友、专业人士或求助热线）。不要给出诊断或医疗建议。"
    ),
}


def format_history(history: list[HistoryMessage]) -> str:
    """把历史消息渲染成可读文本。完整传入，不做截断。"""
    if not history:
        return "（无历史对话）"
    lines = []
    for item in history:
        speaker = "用户" if item.role == "user" else "助手"
        lines.append(f"{speaker}：{item.content}")
    return "\n".join(lines)


def build_emotion_prompt(message: str, retry_feedback: str | None = None) -> str:
    """构造 Agent 1 的用户输入。"""
    parts = [f"用户最新消息：\n{message}"]
    if retry_feedback:
        parts.append(f"\n修正要求：{retry_feedback}\n{REPAIR_INSTRUCTION}")
    return "\n\n".join(parts)


def build_response_prompt(
    message: str,
    emotion_result: EmotionResult,
    history: list[HistoryMessage],
    retry_feedback: str | None = None,
) -> str:
    """构造 Agent 2 的用户输入。"""
    parts = [
        "历史对话：\n" + format_history(history),
        f"用户最新消息：\n{message}",
        (
            "情绪识别结果：\n"
            f'- emotion: {emotion_result.emotion}\n'
            f"- emotion_type: {emotion_result.emotion_type}\n"
            f"- confidence: {emotion_result.confidence}\n"
            f"- reason: {emotion_result.reason}"
        ),
    ]
    if retry_feedback:
        parts.append(f"\n修正要求：{retry_feedback}\n{REPAIR_INSTRUCTION}")
    return "\n\n".join(parts)


def strategy_hint(emotion_result: EmotionResult) -> str:
    """给 Agent 2 的策略提示，随情绪极性给出允许的取值。"""
    if emotion_result.emotion == "positive":
        allowed = '"encouragement" 或 "empathy"'
    else:
        allowed = '"empathy" 或 "support"'
    return f"当前情绪极性为 {emotion_result.emotion}，strategy 请取 {allowed}。"


__all__ = [
    "EMOTION_INSTRUCTIONS",
    "EMOTION_TYPE_LIST",
    "REPAIR_INSTRUCTION",
    "RESPONSE_INSTRUCTIONS",
    "STRATEGY_INSTRUCTIONS",
    "build_emotion_prompt",
    "build_response_prompt",
    "format_history",
    "strategy_hint",
]
