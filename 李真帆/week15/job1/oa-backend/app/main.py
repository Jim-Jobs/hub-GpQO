"""FastAPI 后端进程：接收前端输入，串联进程1（情感识别）与进程2（鼓励/排解）。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app import llm, pipeline
from app.schemas import ChatIn, ChatOut, HealthOut


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时配置大模型 SDK 位置（未配 key 则走规则兜底）
    app.state.llm_configured = llm.setup_llm()
    yield


app = FastAPI(title="情感小助手 API（openai-agents 版）", lifespan=lifespan)


@app.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok", engine="openai-agents", llm_configured=llm.llm_configured())


@app.post("/api/chat", response_model=ChatOut)
async def chat(req: ChatIn):
    try:
        data = await pipeline.run(req.text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"大模型调用失败：{e}")
    return ChatOut(**data)
