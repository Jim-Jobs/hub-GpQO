"""后端进程（OpenAI Agents）：接受前端输入，串联进程 1 → 进程 2。"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from openai_agents_impl.pipeline import run_pipeline  # noqa: E402
from shared.config import FRONTEND_DIR  # noqa: E402
from shared.schemas import ChatRequest, ChatResponse  # noqa: E402

app = FastAPI(title="Sentiment Demo — OpenAI Agents", version="1.0.0")


@app.get("/health")
def health():
    return {"status": "ok", "engine": "openai-agents"}


@app.post("/api/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    try:
        return await run_pipeline(text)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"编排失败: {exc}") from exc


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
    port = int(os.getenv("AGENTS_PORT", "8002"))
    uvicorn.run(
        "openai_agents_impl.server:app",
        host="127.0.0.1",
        port=port,
        reload=True,
    )


if __name__ == "__main__":
    main()
