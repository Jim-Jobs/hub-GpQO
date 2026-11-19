"""FastAPI 后端进程：接收前端输入，串联进程1（情感识别）与进程2（鼓励/排解）。"""
from fastapi import FastAPI, HTTPException

from app import llm
from app.graph import graph
from app.schemas import ChatIn, ChatOut, HealthOut

app = FastAPI(title="情感小助手 API（LangGraph 版）")


@app.get("/health", response_model=HealthOut)
def health():
    return HealthOut(status="ok", engine="langgraph", llm_configured=llm.llm_configured())


@app.post("/api/chat", response_model=ChatOut)
def chat(req: ChatIn):
    try:
        state = graph.invoke({"text": req.text})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"大模型调用失败：{e}")
    return ChatOut(
        sentiment=state["sentiment"],
        score=state["score"],
        mode=state["mode"],
        reply=state["reply"],
        source=state["source"],
    )
