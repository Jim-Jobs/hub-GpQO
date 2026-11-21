from uuid import UUID

from agents import OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from openai import AsyncOpenAI
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.domain.models import PipelineResult
from app.domain.ports import PipelineAdapter
from app.engines.openai_agents.agents import build_reply_agent, build_sentiment_agent
from app.engines.openai_agents.session import AgentSessionFactory
from app.engines.shared.llm import build_async_openai_client
from app.engines.shared.parser import parse_sentiment


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
    return "" if content is None else str(content)


class OpenAIAgentsPipeline(PipelineAdapter):
    name = "openai-agents"

    def __init__(self, settings: Settings, engine: AsyncEngine) -> None:
        self._client: AsyncOpenAI = build_async_openai_client(settings)
        self._model = OpenAIChatCompletionsModel(
            model=settings.qwen_model,
            openai_client=self._client,
        )
        self._sessions = AgentSessionFactory(engine)

    async def start(self) -> None:
        set_tracing_disabled(True)

    async def run(self, conversation_id: UUID, message: str) -> PipelineResult:
        sentiment_agent = build_sentiment_agent(self._model, message)
        sentiment_result = await Runner.run(sentiment_agent, message)
        sentiment = parse_sentiment(_content_to_text(sentiment_result.final_output))

        reply_agent = build_reply_agent(self._model, sentiment, message)
        session = await self._sessions.create(conversation_id)
        reply_result = await Runner.run(reply_agent, message, session=session)
        reply = _content_to_text(reply_result.final_output).strip()

        return PipelineResult(sentiment=sentiment, reply=reply)

    async def aclose(self) -> None:
        await self._client.close()


if __name__ == "__main__":
    print("adapter:", OpenAIAgentsPipeline.name)
