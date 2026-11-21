from uuid import UUID

from langchain_core.messages import HumanMessage

from app.config import Settings
from app.domain.enums import Sentiment
from app.domain.models import PipelineResult
from app.domain.ports import PipelineAdapter
from app.engines.langgraph.checkpointer import CheckpointerLifecycle
from app.engines.langgraph.graph import build_graph


class LangGraphPipeline(PipelineAdapter):
    name = "langgraph"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._checkpointer = CheckpointerLifecycle(settings)
        self._graph = None

    async def start(self) -> None:
        saver = await self._checkpointer.__aenter__()
        self._graph = build_graph(self._settings, saver)

    async def run(self, conversation_id: UUID, message: str) -> PipelineResult:
        if self._graph is None:
            raise RuntimeError("LangGraphPipeline.start() 尚未调用")
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=message)]},
            config={"configurable": {"thread_id": str(conversation_id)}},
        )
        sentiment = Sentiment(result.get("sentiment", Sentiment.NEGATIVE.value))
        return PipelineResult(sentiment=sentiment, reply=result.get("reply", ""))

    async def aclose(self) -> None:
        await self._checkpointer.__aexit__(None, None, None)
        self._graph = None


if __name__ == "__main__":
    print("adapter:", LangGraphPipeline.name)
