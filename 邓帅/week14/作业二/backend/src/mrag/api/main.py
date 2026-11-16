"""FastAPI 入口：上传/状态/删除/重试 + SSE 问答 + 图片静态服务。

启动：cd 邓帅/week14/作业二 && PYTHONPATH=src uvicorn mrag.api.main:app --port 8001
"""

import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from mrag import zilliz_store
from mrag.api import documents
from mrag.api.retrieval import search_context
from mrag.api.vl import build_vl_messages
from mrag.config import DATA_DIR, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("mrag.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    s.ensure_dirs()
    app.state.kafka = None
    zilliz_store.ensure_collection()
    log.info("API 就绪: kafka=%s zilliz=%s", s.kafka_bootstrap, s.zilliz_collection)
    yield
    try:
        if app.state.kafka is not None:
            app.state.kafka.close()
    except Exception:  # noqa: BLE001
        pass


app = FastAPI(title="week14 multimodal rag", lifespan=lifespan)
app.include_router(documents.router)
app.mount("/files", StaticFiles(directory=DATA_DIR), name="files")


class ChatRequest(BaseModel):
    history: list[dict] = []
    question: str
    image: str | None = None  # 裸 base64（无 data: 前缀）


@app.post("/api/chat")
async def chat(req: ChatRequest):
    if not req.question.strip():
        return EventSourceResponse(_error_stream("问题不能为空"))
    return EventSourceResponse(_chat_stream(req))


async def _chat_stream(req: ChatRequest):
    """检索 -> VL 流式 -> SSE 事件流。"""
    import asyncio

    from openai import AsyncOpenAI

    s = get_settings()
    try:
        ctx = await asyncio.to_thread(search_context, req.question, req.image)
        messages = build_vl_messages(req.history, req.question, ctx["blocks"], req.image, ctx["image_urls"])
        refs = sorted({b["file_name"] for b in ctx["blocks"]})
    except Exception as e:  # noqa: BLE001
        yield _sse("error", {"message": f"检索失败: {e}"})
        return

    yield _sse("refs", {"refs": refs, "images": ctx["image_urls"][:3]})

    client = AsyncOpenAI(api_key=s.api_key, base_url=s.base_url)
    try:
        stream = await client.chat.completions.create(
            model=s.vl_model,
            messages=messages,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and (delta := chunk.choices[0].delta.content):
                yield _sse("delta", {"text": delta})
    except Exception as e:  # noqa: BLE001 - 上游模型错误转可读事件
        log.warning("VL 调用失败: %s", e)
        yield _sse("error", {"message": f"模型调用失败: {str(e)[:300]}"})
        return
    yield _sse("done", {"refs": refs})


def _sse(event: str, data: dict) -> dict:
    return {"event": event, "data": json.dumps(data, ensure_ascii=False)}


async def _error_stream(msg: str):
    yield _sse("error", {"message": msg})
    yield _sse("done", {"refs": []})
