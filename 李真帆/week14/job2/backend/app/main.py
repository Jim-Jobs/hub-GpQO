import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import database
from app.api import chat, documents, events, sessions
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    await events.hub.start()
    yield
    await events.hub.stop()


app = FastAPI(title="多模态 RAG 问答系统", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

settings.ensure_dirs()
app.mount("/static", StaticFiles(directory=settings.data_dir), name="static")

app.include_router(documents.router)
app.include_router(sessions.router)
app.include_router(chat.router)
app.include_router(events.router)


@app.get("/api/health")
def health():
    return {"ok": True}


# 单机部署：若前端已构建(dist)，由本服务直接托管，监听 8080 对外即可；
# 非 /api 路径命中文件则返回文件，否则回落 index.html（支持 SPA 刷新深链）
_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.is_dir():
    from fastapi.responses import FileResponse

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        target = (_frontend_dist / full_path) if full_path else None
        if target and target.is_file() and _frontend_dist.resolve() in target.resolve().parents:
            return FileResponse(target)
        return FileResponse(_frontend_dist / "index.html")
