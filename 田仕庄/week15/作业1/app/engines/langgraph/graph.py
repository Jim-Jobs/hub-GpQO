from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages

from app.config import Settings
from app.engines.shared.llm import build_chat_openai
from app.engines.shared.parser import parse_sentiment
from app.engines.shared.prompts import build_reply_prompt, build_sentiment_prompt
from app.domain.enums import Sentiment


class ChatState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    sentiment: str
    reply: str


def _content_to_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and "text" in item:
                parts.append(str(item["text"]))
        return "".join(parts)
    return str(content)


def _last_user_text(messages: list[AnyMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return _content_to_text(message.content)
    return ""


def build_graph(settings: Settings, checkpointer: BaseCheckpointSaver):
    llm: ChatOpenAI = build_chat_openai(settings)

    async def classify(state: ChatState) -> dict:
        message = _last_user_text(state.get("messages", []))
        response = await llm.ainvoke(
            [
                SystemMessage(content=build_sentiment_prompt(message)),
                *state.get("messages", []),
            ]
        )
        sentiment = parse_sentiment(_content_to_text(response.content))
        return {"sentiment": sentiment.value}

    async def generate_reply(state: ChatState, sentiment: Sentiment) -> dict:
        message = _last_user_text(state.get("messages", []))
        response = await llm.ainvoke(
            [
                SystemMessage(content=build_reply_prompt(sentiment, message)),
                *state.get("messages", []),
            ]
        )
        reply = _content_to_text(response.content).strip()
        return {"messages": [AIMessage(content=reply)], "reply": reply}

    async def encourage(state: ChatState) -> dict:
        return await generate_reply(state, Sentiment.POSITIVE)

    async def comfort(state: ChatState) -> dict:
        return await generate_reply(state, Sentiment.NEGATIVE)

    def route(state: ChatState) -> Literal["encourage", "comfort"]:
        return "encourage" if state.get("sentiment") == Sentiment.POSITIVE.value else "comfort"

    builder = StateGraph(ChatState)
    builder.add_node("classify", classify)
    builder.add_node("encourage", encourage)
    builder.add_node("comfort", comfort)
    builder.add_edge(START, "classify")
    builder.add_conditional_edges(
        "classify", route, {"encourage": "encourage", "comfort": "comfort"}
    )
    builder.add_edge("encourage", END)
    builder.add_edge("comfort", END)
    return builder.compile(checkpointer=checkpointer)


if __name__ == "__main__":
    print("graph nodes: classify -> encourage | comfort")
    print("state keys:", list(ChatState.__annotations__))
