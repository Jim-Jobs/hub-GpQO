"""FastAPI 入口:SQL Agent 问答 API + 托管 frontend/dist(build 后的演示模式)。"""
import logging
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import history
from app.api.routes import router
from app.config import DB_PATH, DB_URL
from app.db_parser import DBParser

logging.basicConfig(level=logging.INFO)

# 作业一根目录(app/ 的上两级),frontend 构建产物相对它定位
HW11_DIR = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时完成第 1 步「数据库解析」并缓存,建 history 表。"""
    history.init_db()
    app.state.parser = DBParser(DB_URL)
    # 只读连接:第 4 步 EXPLAIN 校验与第 6 步执行共用(mode=ro 双保险)
    app.state.ro_conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    yield
    app.state.ro_conn.close()


app = FastAPI(title="SQL Agent 智能问答", lifespan=lifespan)
app.include_router(router, prefix="/api")

# 演示模式:托管前端构建产物(存在时);开发模式走 vite dev proxy
_dist = HW11_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
