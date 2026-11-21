from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.domain.ports import PipelineAdapter
from app.engines.langgraph.pipeline import LangGraphPipeline
from app.engines.openai_agents.pipeline import OpenAIAgentsPipeline


async def create_pipeline(settings: Settings, engine: AsyncEngine) -> PipelineAdapter:
    """唯一知道当前引擎的地方。"""
    if settings.pipeline_engine == "langgraph":
        pipeline: PipelineAdapter = LangGraphPipeline(settings)
    else:
        pipeline = OpenAIAgentsPipeline(settings, engine)
    await pipeline.start()
    return pipeline


if __name__ == "__main__":
    print("supported engines: langgraph, openai-agents")
