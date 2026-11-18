"""后端进程（LangGraph）：接受前端输入，串联进程 1 → 进程 2。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# 保证从任意 cwd 启动都能找到 shared / langgraph_impl
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from langgraph_impl.graph import run_pipeline  # noqa: E402
from shared.config import FRONTEND_DIR  # noqa: E402
from shared.schemas import ChatRequest, ChatResponse, SentimentLabel  # noqa: E402

app = FastAPI(title="Sentiment Demo — LangGraph", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "engine": "langgraph"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    try:
        state = run_pipeline(text)
    except Exception as exc:  # noqa: BLE001 — 统一成 API 错误
        raise HTTPException(status_code=500, detail=f"编排失败: {exc}") from exc

    return ChatResponse(
        text=text,
        sentiment=SentimentLabel(state["sentiment"]),
        reason=state["reason"],
        reply=state["reply"],
    )


@app.get("/")
def index():
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html 不存在")
    return FileResponse(index_file)


if FRONTEND_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


def main() -> None:
    import os

    import uvicorn

    os.chdir(_ROOT)
    port = int(os.getenv("LANGGRAPH_PORT", "8001"))
    uvicorn.run(
        "langgraph_impl.server:app",
        host="127.0.0.1",
        port=port,
        reload=True,
    )


if __name__ == "__main__":
    main()
