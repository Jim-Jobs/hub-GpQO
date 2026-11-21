from agents import Agent, Runner
from pydantic import BaseModel, Field

from .schemas import EmotionResult
from .settings import model_name


class SupportReply(BaseModel):
    reply: str = Field(description="面向用户的简短中文回复")


def run_agents(text: str) -> dict[str, str]:
    """依次运行情感识别 Agent 与回应 Agent，体现两个独立逻辑模块。"""
    model = model_name()
    classifier = Agent(
        name="情感识别模块",
        instructions=(
            "判断用户文本的情感。positive=正向，negative=负向，neutral=中性。"
            "只输出情感类别和简短判断依据，不生成安慰或鼓励回复。"
        ),
        output_type=EmotionResult,
        model=model,
    )
    classification = Runner.run_sync(classifier, text)
    emotion: EmotionResult = classification.final_output

    if emotion.emotion == "positive":
        instructions = "用户情感偏正向。请结合其表达给予具体、自然的肯定和鼓励，用中文简短回复，不要夸大。"
        agent_name = "正向鼓励模块"
    else:
        instructions = "用户情感为负向或中性。请先表达理解，再给出温和、可执行的小建议或邀请对方继续表达。用中文简短回复，不要评判。"
        agent_name = "负向排解模块"

    responder = Agent(
        name=agent_name,
        instructions=instructions,
        output_type=SupportReply,
        model=model,
    )
    response = Runner.run_sync(responder, f"用户原文：{text}\n情感类别：{emotion.emotion}\n判断依据：{emotion.reason}")
    final: SupportReply = response.final_output
    return {"emotion": emotion.emotion, "reason": emotion.reason, "reply": final.reply}
