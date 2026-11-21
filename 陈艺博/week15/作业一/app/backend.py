import os
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .schemas import ChatRequest, ChatResponse


@lru_cache(maxsize=1)
def _langgraph():
    from .langgraph_flow import run_langgraph

    return run_langgraph


@lru_cache(maxsize=1)
def _agents():
    from .agents_flow import run_agents

    return run_agents


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if not os.getenv("OPENAI_API_KEY"):
            # API can still start; a clear error is returned when a request arrives.
            pass
        yield

    api = FastAPI(title="情感识别与回应", version="1.0.0", lifespan=lifespan)

    @api.get("/", include_in_schema=False)
    def home():
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @api.get("/health")
    def health():
        return {"status": "ok"}

    def execute(framework: str, request: ChatRequest) -> ChatResponse:
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(status_code=503, detail="未配置 OPENAI_API_KEY，请复制 .env 为 .env 并填写密钥。")
        try:
            runner = _langgraph() if framework == "langgraph" else _agents()
            result = runner(request.text)
            return ChatResponse(framework=framework, **result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"模型调用失败：{exc}") from exc

    @api.post("/api/langgraph", response_model=ChatResponse)
    def chat_langgraph(request: ChatRequest):
        return execute("langgraph", request)

    @api.post("/api/agents", response_model=ChatResponse)
    def chat_agents(request: ChatRequest):
        return execute("agents", request)

    return api


app = create_app()
