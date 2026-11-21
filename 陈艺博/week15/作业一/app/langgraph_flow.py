from typing import Literal, TypedDict

from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from .schemas import EmotionResult
from .settings import model_name, openai_base_url


class SupportState(TypedDict, total=False):
    text: str
    emotion: str
    reason: str
    reply: str


def build_graph():
    llm = ChatOpenAI(model=model_name(), temperature=0, base_url=openai_base_url())
    classifier = llm.with_structured_output(EmotionResult)

    def recognize_emotion(state: SupportState) -> dict:
        result = classifier.invoke([
            ("system", "你是情感识别模块。仅根据用户文本判断情感。positive=正向，negative=负向，neutral=中性；不要提供回复。"),
            ("human", state["text"]),
        ])
        return {"emotion": result.emotion, "reason": result.reason}

    def route_by_emotion(state: SupportState) -> Literal["encourage", "comfort"]:
        return "encourage" if state["emotion"] == "positive" else "comfort"

    def encourage(state: SupportState) -> dict:
        answer = llm.invoke([
            ("system", "你是温暖、真诚的支持者。用户情感偏正向，请结合对方表达给予具体、自然的肯定和鼓励。用中文简短回复，不要夸大。"),
            ("human", state["text"]),
        ])
        return {"reply": answer.content}

    def comfort(state: SupportState) -> dict:
        answer = llm.invoke([
            ("system", "你是温暖、耐心的倾听者。用户情感为负向或中性，请先表达理解，再给出温和、可执行的小建议或邀请对方继续表达。用中文简短回复，不要评判。"),
            ("human", state["text"]),
        ])
        return {"reply": answer.content}

    graph = StateGraph(SupportState)
    graph.add_node("emotion_recognition", recognize_emotion)
    graph.add_node("encourage", encourage)
    graph.add_node("comfort", comfort)
    graph.add_edge(START, "emotion_recognition")
    graph.add_conditional_edges("emotion_recognition", route_by_emotion)
    graph.add_edge("encourage", END)
    graph.add_edge("comfort", END)
    return graph.compile()


def run_langgraph(text: str) -> dict[str, str]:
    result = build_graph().invoke({"text": text})
    return {"emotion": result["emotion"], "reason": result["reason"], "reply": result["reply"]}
