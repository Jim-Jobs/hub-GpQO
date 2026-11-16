"""FastAPI 前端服务：上传 / 状态 / 问答 / 历史 / 事件流 API + 静态网页。

所有耗时任务都通过 Kafka 交给 app.worker：
    POST /api/upload -> mmrag.pdf.tasks
    POST /api/qa     -> mmrag.qa.requests
回答与状态通过后台消费线程从 mmrag.qa.responses / mmrag.pdf.status / mmrag.events 收回 Redis。
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import messaging, rag
from .config import STATUS_FAILED, STATUS_PENDING, WEB_DIR, settings
from .store import get_store

logger = logging.getLogger("mmrag.api")
_stop = threading.Event()

MAX_PDF_BYTES = 100 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def api_consumer() -> None:
    """消费 Kafka 回答与事件，写入 Redis 供前端轮询。"""
    topics = settings.topics
    store = get_store()

    def handler(topic: str, payload: dict) -> None:
        if topic == topics["qa_responses"]:
            store.apply_answer(payload)
        elif topic == topics["parse_status"]:
            store.push_event(
                {
                    "source": "解析 Worker",
                    "action": payload.get("status", "状态更新"),
                    "detail": payload.get("detail", ""),
                    "ts": payload.get("ts", time.time()),
                }
            )
        else:
            store.push_event(payload)

    messaging.consume_loop(
        "mmrag-api",
        [topics["qa_responses"], topics["parse_status"], topics["events"]],
        handler,
        _stop,
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    for sub in ("uploads", "docs", "questions", "tmp", "index"):
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)
    try:
        messaging.ensure_topics()
        logger.info("Kafka Topic 就绪：%s", ", ".join(settings.topics.values()))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kafka 暂不可用，后台线程会持续重连：%s", exc)
    _stop.clear()
    thread = threading.Thread(target=api_consumer, name="api-consumer", daemon=True)
    thread.start()
    yield
    _stop.set()


app = FastAPI(title="Kafka 多模态 RAG 问答", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "mode": "local" if settings.local_mode else "kafka",
        "redis": get_store().ping(),
        "kafka": messaging.check_broker(),
        "llm": settings.llm_configured,
        "llm_model": settings.llm_model,
        "parser_backend": settings.parser_backend,
        "mineru_cli": shutil.which(settings.mineru_bin) is not None,
        "pymupdf": importlib.util.find_spec("fitz") is not None,
        "index": rag.index_stats(),
    }


@app.get("/api/config")
def config():
    return {
        "mode": "local" if settings.local_mode else "kafka",
        "topics": settings.topics,
        "llm_model": settings.llm_model,
        "embed_model": settings.embed_model,
        "parser_backend": settings.parser_backend,
        "vision_enabled": settings.vision_enabled,
    }


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """功能 1：一次上传多个 PDF，每个文件生成一条 Kafka 解析任务。"""
    store = get_store()
    results = []
    for upload_file in files:
        filename = Path(upload_file.filename or "untitled.pdf").name
        if not filename.lower().endswith(".pdf"):
            results.append({"filename": filename, "status": STATUS_FAILED, "error": "仅支持 PDF 文件"})
            continue
        data = await upload_file.read()
        if len(data) > MAX_PDF_BYTES:
            results.append({"filename": filename, "status": STATUS_FAILED, "error": "文件超过 100MB"})
            continue
        if not data:
            results.append({"filename": filename, "status": STATUS_FAILED, "error": "空文件"})
            continue

        doc_id = uuid.uuid4().hex[:12]
        relative_path = f"uploads/{doc_id}.pdf"
        (settings.data_dir / relative_path).write_bytes(data)
        store.create_doc(doc_id, filename, len(data), STATUS_PENDING)
        try:
            messaging.send(
                settings.topics["parse_tasks"],
                {"doc_id": doc_id, "filename": filename, "pdf_path": relative_path, "ts": time.time()},
                key=doc_id,
            )
            messaging.publish_event("网页", "上传 PDF", f"{filename} → {settings.topics['parse_tasks']}", doc_id=doc_id)
            results.append({"id": doc_id, "filename": filename, "status": STATUS_PENDING, "size": len(data)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("上传任务发送失败")
            store.update_doc(doc_id, status=STATUS_FAILED, error=f"Kafka 发送失败：{exc}")
            results.append({"id": doc_id, "filename": filename, "status": STATUS_FAILED, "error": str(exc)})
    return {"documents": results, "topic": settings.topics["parse_tasks"]}


@app.get("/api/documents")
def documents():
    """功能 2：前端轮询获取「待解析 / 解析中 / 解析完成」状态。"""
    return {"documents": get_store().list_docs()}


@app.get("/api/documents/{doc_id}/content")
def document_content(doc_id: str):
    content_path = settings.data_dir / "docs" / doc_id / "content.md"
    if not content_path.is_file():
        raise HTTPException(status_code=404, detail="文档尚未解析完成")
    meta_path = settings.data_dir / "docs" / doc_id / "meta.json"
    assets = []
    if meta_path.is_file():
        try:
            assets = json.loads(meta_path.read_text(encoding="utf-8")).get("assets", [])
        except (ValueError, OSError):
            pass
    return {"doc_id": doc_id, "markdown": content_path.read_text(encoding="utf-8"), "assets": assets}


@app.post("/api/qa")
async def ask(
    question: str = Form(...),
    session_id: str = Form("default"),
    doc_ids: str = Form(""),
    use_vision: str = Form("true"),
    image: UploadFile | None = File(None),
):
    """功能 3：文字提问 / 图+问一起提交，经 Kafka 交给问答 Worker。"""
    text = question.strip()
    if not text:
        raise HTTPException(status_code=400, detail="问题不能为空")
    store = get_store()
    request_id = uuid.uuid4().hex[:12]
    image_path = ""
    user_image_url = ""
    if image and image.filename:
        suffix = Path(image.filename).suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            suffix = ".png"
        data = await image.read()
        if data:
            image_path = f"questions/{request_id}{suffix}"
            (settings.data_dir / image_path).write_bytes(data)
            user_image_url = settings.media_url(image_path)

    store.create_qa(request_id, session_id, text)
    store.append_history(session_id, "user", text, [user_image_url] if user_image_url else [])
    selected = [item.strip() for item in doc_ids.split(",") if item.strip()]
    payload = {
        "request_id": request_id,
        "session_id": session_id,
        "question": text,
        "doc_ids": selected,
        "image_paths": [image_path] if image_path else [],
        "use_vision": use_vision.strip().lower() in {"1", "true", "yes", "on"},
        "ts": time.time(),
    }
    try:
        messaging.send(settings.topics["qa_requests"], payload, key=request_id)
        label = "图文提问" if image_path else "文字提问"
        messaging.publish_event("网页", label, text[:60], request_id=request_id)
        status = "排队中"
        error = ""
    except Exception as exc:  # noqa: BLE001
        logger.exception("问答任务发送失败")
        status, error = "失败", str(exc)
        store.update_qa(request_id, status=status, error=error)
    return {"request_id": request_id, "status": status, "error": error}


@app.get("/api/qa/{request_id}")
def qa_result(request_id: str):
    state = get_store().get_qa(request_id)
    if state is None:
        raise HTTPException(status_code=404, detail="问答任务不存在或已过期")
    return state


@app.get("/api/history")
def history(session_id: str = "default"):
    return {"messages": get_store().get_history(session_id)}


@app.delete("/api/history")
def clear_history(session_id: str = "default"):
    get_store().clear_history(session_id)
    return {"ok": True}


@app.get("/api/events")
def events(limit: int = 30):
    return {"events": get_store().list_events(limit)}


@app.get("/api/index")
def index_stats():
    return rag.index_stats()


app.mount("/static", StaticFiles(directory=str(WEB_DIR), check_dir=False), name="static")
app.mount("/media", StaticFiles(directory=str(settings.data_dir), check_dir=False), name="media")
