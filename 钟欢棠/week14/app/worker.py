"""后端 Worker：两个独立消费组分别处理 PDF 解析任务与多模态问答任务。

    mmrag.pdf.tasks   -> 解析 Worker（MinerU / PyMuPDF -> 分块入库 -> 更新状态）
    mmrag.qa.requests -> 问答 Worker（检索 -> 图文上下文 -> LLM -> mmrag.qa.responses）
"""

from __future__ import annotations

import logging
import signal
import threading
import time

from . import llm, messaging, parser, rag
from .config import STATUS_DONE, STATUS_FAILED, STATUS_PARSING, settings
from .store import get_store

logger = logging.getLogger("mmrag.worker")
_stop = threading.Event()


def _publish_status(doc_id: str, filename: str, status: str, detail: str) -> None:
    payload = {"doc_id": doc_id, "filename": filename, "status": status, "detail": detail, "ts": time.time()}
    # 状态事件只走 mmrag.pdf.status，由 API 的消费线程写入前端事件流，避免重复展示
    try:
        messaging.send(settings.topics["parse_status"], payload, key=doc_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("状态消息发送失败：%s", exc)
        messaging.publish_event("解析 Worker", status, detail, doc_id=doc_id)


def handle_parse(payload: dict) -> None:
    doc_id = payload.get("doc_id", "")
    store = get_store()
    doc = store.get_doc(doc_id) or {}
    filename = payload.get("filename") or doc.get("filename", "PDF")
    pdf_path = settings.data_dir / str(payload.get("pdf_path", ""))
    if not pdf_path.is_file():
        raise FileNotFoundError(f"找不到上传的 PDF：{pdf_path}")

    store.update_doc(doc_id, status=STATUS_PARSING, started_at=time.time(), error="")
    _publish_status(doc_id, filename, STATUS_PARSING, f"{filename} 开始解析")
    try:
        result = parser.parse_pdf(pdf_path, doc_id, settings.data_dir / "docs" / doc_id)
        chunks = rag.build_chunks(result["markdown"], doc_id, filename)
        count = rag.add_document(doc_id, chunks)
        store.update_doc(
            doc_id,
            status=STATUS_DONE,
            chunk_count=count,
            image_count=len(result["assets"]),
            pages=result["pages"],
            parser=result["backend"],
            finished_at=time.time(),
            error="",
        )
        detail = f"{filename} · {count} 个片段 · {len(result['assets'])} 张图片 · {result['backend']}"
        _publish_status(doc_id, filename, STATUS_DONE, detail)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] 解析失败", doc_id)
        store.update_doc(doc_id, status=STATUS_FAILED, error=str(exc), finished_at=time.time())
        _publish_status(doc_id, filename, STATUS_FAILED, f"{filename}：{exc}")


def handle_qa(payload: dict) -> None:
    request_id = payload.get("request_id", "")
    session_id = payload.get("session_id", "default")
    question = str(payload.get("question", "")).strip()
    store = get_store()
    if not request_id or not question:
        return

    store.update_qa(request_id, status="生成中")
    messaging.publish_event("问答 Worker", "生成回答中", question[:60], request_id=request_id)
    try:
        doc_ids = payload.get("doc_ids") or None
        ranked = rag.retrieve(question, top_k=settings.top_k, doc_ids=doc_ids)
        doc_names = {d["id"]: d.get("filename", "文档") for d in store.list_docs()}
        history = store.get_history(session_id)
        image_paths = [str(settings.data_dir / p) for p in payload.get("image_paths", []) if p]
        use_vision = payload.get("use_vision", True) is not False
        try:
            result = llm.answer(question, ranked, doc_names, image_paths, history, use_vision=use_vision)
            status = "done"
        except Exception as exc:  # noqa: BLE001
            logger.exception("[%s] LLM 调用失败", request_id)
            result = {"answer": f"回答生成失败：{exc}", "mode": "error", "sources": [], "images": []}
            status = "error"
    except Exception as exc:  # noqa: BLE001
        logger.exception("[%s] 检索失败", request_id)
        result = {"answer": f"检索失败：{exc}", "mode": "error", "sources": [], "images": []}
        status = "error"

    response = {
        "request_id": request_id,
        "session_id": session_id,
        "question": question,
        "status": status,
        "answer": result["answer"],
        "mode": result["mode"],
        "sources": result["sources"],
        "images": result["images"],
        "error": "" if status == "done" else result["answer"],
        "ts": time.time(),
    }
    try:
        messaging.send(settings.topics["qa_responses"], response, key=request_id)
        messaging.publish_event("问答 Worker", "回答完成", result["mode"], request_id=request_id)
    except Exception as exc:  # noqa: BLE001 - Kafka 故障时直接落库，避免前端一直等待
        logger.warning("回答消息发送失败，直接写入 Redis：%s", exc)
        store.apply_answer(response)


def dispatch(topic: str, payload: dict) -> None:
    if topic == settings.topics["parse_tasks"]:
        handle_parse(payload)
    elif topic == settings.topics["qa_requests"]:
        handle_qa(payload)


def start_workers(stop: threading.Event | None = None) -> list[threading.Thread]:
    """启动解析组与问答组两个消费线程；本地模式下由 run.py 在同一进程内调用。"""
    stop = stop or _stop
    topics = settings.topics
    threads = [
        threading.Thread(
            target=messaging.consume_loop,
            args=("mmrag-pdf-parser", [topics["parse_tasks"]], dispatch, stop),
            name="pdf-parser",
            daemon=True,
        ),
        threading.Thread(
            target=messaging.consume_loop,
            args=("mmrag-qa-worker", [topics["qa_requests"]], dispatch, stop),
            name="qa-worker",
            daemon=True,
        ),
    ]
    for thread in threads:
        thread.start()
    logger.info("Worker 已启动：解析组 mmrag-pdf-parser / 问答组 mmrag-qa-worker")
    return threads


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        messaging.ensure_topics()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Topic 初始化失败（消费循环会自动重试）：%s", exc)

    threads = start_workers(_stop)
    try:
        while not _stop.wait(0.5):
            pass
    except KeyboardInterrupt:
        pass
    finally:
        _stop.set()
        for thread in threads:
            thread.join(timeout=3)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, lambda *_: _stop.set())
    signal.signal(signal.SIGTERM, lambda *_: _stop.set())
    main()
