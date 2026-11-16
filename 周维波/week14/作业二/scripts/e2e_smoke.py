"""端到端冒烟脚本：造样例 PDF -> 上传 -> 观察状态流转 -> 文本问答 -> 图问。

前置：Kafka / API / worker 均已启动（见 README 的“快速开始”）。

    python scripts/e2e_smoke.py --base-url http://127.0.0.1:8000

退出码 0 表示全部断言通过。
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pymupdf as fitz  # PyMuPDF
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PROJECT_ROOT / "data" / "samples"


def _chart_architecture(path: Path) -> None:
    width, height = 900, 420
    image = Image.new("RGB", (width, height), "#f7fafc")
    draw = ImageDraw.Draw(image)

    boxes = [
        ("Upload API", (40, 60, 250, 160)),
        ("Kafka", (310, 60, 520, 160)),
        ("Parse Worker", (580, 60, 840, 160)),
        ("Vector Store", (310, 240, 520, 350)),
    ]
    for label, box in boxes:
        draw.rectangle(box, fill="#e6f4f1", outline="#0f766e", width=3)
        draw.text((box[0] + 14, box[1] + 42), label, fill="#0f3d3a")

    draw.line([(250, 110), (310, 110)], fill="#0f766e", width=4)
    draw.line([(520, 110), (580, 110)], fill="#0f766e", width=4)
    draw.line([(700, 160), (700, 210), (420, 210), (420, 240)], fill="#0f766e", width=4)
    draw.line([(310, 295), (250, 295), (250, 160)], fill="#0f766e", width=4)

    draw.text((40, 380), "Figure 1: Kafka decouples upload from slow parsing", fill="#475467")
    image.save(path, format="PNG")


def _chart_revenue(path: Path) -> None:
    width, height = 860, 420
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)

    quarters = [("Q1", 1200), ("Q2", 1500), ("Q3", 2100), ("Q4", 1800)]
    max_value = 2400
    base_y = 360
    bar_width = 90

    draw.line([(90, base_y), (760, base_y)], fill="#98a2b3", width=2)
    draw.line([(90, 60), (90, base_y)], fill="#98a2b3", width=2)

    for index, (label, value) in enumerate(quarters):
        left = 150 + index * 160
        top = base_y - int((value / max_value) * 280)
        color = "#0f766e" if label == "Q3" else "#7fb7ae"
        draw.rectangle((left, top, left + bar_width, base_y), fill=color)
        draw.text((left + 20, base_y - 20), label, fill="#344054")
        draw.text((left + 20, top - 22), str(value), fill="#0f3d3a")

    draw.text((90, 30), "Revenue by quarter (2025, unit: 10k CNY)", fill="#344054")
    image.save(path, format="PNG")


def _new_pdf(path: Path) -> fitz.Document:
    document = fitz.open()
    return document


def _insert_text(page: fitz.Page, point: tuple[float, float], text: str, size: int = 12) -> None:
    try:
        page.insert_text(point, text, fontsize=size, fontname="china-s", color=(0.12, 0.16, 0.2))
    except Exception:
        page.insert_text(point, text, fontsize=size, fontname="helv", color=(0.12, 0.16, 0.2))


def build_samples() -> list[Path]:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    architecture_png = SAMPLE_DIR / "chart_architecture.png"
    revenue_png = SAMPLE_DIR / "chart_revenue.png"
    if not architecture_png.exists():
        _chart_architecture(architecture_png)
    if not revenue_png.exists():
        _chart_revenue(revenue_png)

    docs: list[Path] = []

    # 文档 1：架构说明（第 2 页嵌架构图）
    doc1 = SAMPLE_DIR / "多模态RAG架构说明.pdf"
    pdf = _new_pdf(doc1)
    page = pdf.new_page()
    _insert_text(page, (60, 70), "多模态 RAG 问答系统架构说明", 20)
    _insert_text(page, (60, 120), "系统把文档上传与文档解析解耦：上传接口只负责落盘并把任务投递到 Kafka。", 12)
    _insert_text(page, (60, 145), "解析 Worker 以消费者身份慢慢处理，单个 PDF 通常需要数十秒到数分钟。", 12)
    _insert_text(page, (60, 170), "解析使用 MinerU 输出 markdown 与图片，再切分为文本切片并做向量化。", 12)
    page = pdf.new_page()
    _insert_text(page, (60, 60), "图 1 架构图", 16)
    page.insert_image(fitz.Rect(60, 80, 520, 310), filename=str(architecture_png))
    _insert_text(page, (60, 330), "架构图：上传服务 → Kafka → 解析 Worker → 向量库。", 12)
    _insert_text(page, (60, 355), "文本向量用 bge 编码；图片向量用 CLIP 编码，因此支持以文搜图和以图搜图。", 12)
    pdf.save(str(doc1))
    pdf.close()
    docs.append(doc1)

    # 文档 2：季度营收（第 1 页嵌柱状图）
    doc2 = SAMPLE_DIR / "季度营收报告2025.pdf"
    pdf = _new_pdf(doc2)
    page = pdf.new_page()
    _insert_text(page, (60, 60), "2025 年季度营收报告", 18)
    _insert_text(page, (60, 100), "全年营收合计 6600 万元，其中第三季度最高。", 12)
    page.insert_image(fitz.Rect(60, 120, 520, 360), filename=str(revenue_png))
    _insert_text(page, (60, 380), "季度营收柱状图：Q1 1200，Q2 1500，Q3 2100，Q4 1800（单位：万元）。", 12)
    page = pdf.new_page()
    _insert_text(page, (60, 70), "结论", 16)
    _insert_text(page, (60, 100), "第三季度环比增长 40%，主要由企业知识库产品放量带动。", 12)
    _insert_text(page, (60, 125), "第四季度回落 14%，原因是项目验收集中在第三季度。", 12)
    pdf.save(str(doc2))
    pdf.close()
    docs.append(doc2)

    return docs


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            continue
        try:
            events.append((event, json.loads("\n".join(data_lines))))
        except json.JSONDecodeError:
            continue
    return events


def listen_status_events(
    base_url: str,
    sink: list[tuple[str, dict]],
    stop_event: threading.Event,
    ready_event: threading.Event,
) -> None:
    """订阅 /api/events，把 SSE 事件收进 sink（用于验证“解析中”这类瞬时状态）。"""
    try:
        timeout = httpx.Timeout(10.0, read=None)
        with httpx.Client(timeout=timeout) as client:
            with client.stream("GET", f"{base_url.rstrip('/')}/api/events") as response:
                ready_event.set()
                event_name = "message"
                for line in response.iter_lines():
                    if stop_event.is_set():
                        return
                    if line.startswith("event:"):
                        event_name = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            continue
                        sink.append((event_name, payload))
                        event_name = "message"
    except Exception as exc:  # 连接异常不应该掩盖真正的断言结果
        sink.append(("error", {"message": f"{type(exc).__name__}: {exc}"}))


def wait_for_completion(client: httpx.Client, doc_ids: list[str], timeout: float) -> dict[str, set[str]]:
    seen: dict[str, set[str]] = {doc_id: set() for doc_id in doc_ids}
    deadline = time.time() + timeout
    last_print = 0.0
    while time.time() < deadline:
        response = client.get("/api/documents")
        response.raise_for_status()
        documents = {item["id"]: item for item in response.json()["documents"]}
        pending = False
        for doc_id in doc_ids:
            item = documents.get(doc_id)
            if not item:
                continue
            seen[doc_id].add(item["status"])
            if item["status"] in {"pending", "parsing"}:
                pending = True
        if time.time() - last_print > 4:
            last_print = time.time()
            summary = ", ".join(
                f"{documents.get(doc_id, {}).get('filename', doc_id)[:18]}:"
                f"{documents.get(doc_id, {}).get('status_label', '?')}"
                f"({documents.get(doc_id, {}).get('progress', 0)}%)"
                for doc_id in doc_ids
            )
            print(f"  [状态] {summary}", flush=True)
        if not pending:
            return seen
        time.sleep(1.2)
    raise TimeoutError(f"等待解析完成超时（{timeout}s）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=float, default=240.0)
    args = parser.parse_args()

    failures: list[str] = []

    def check(condition: bool, message: str) -> None:  # noqa: ANN001
        if condition:
            print(f"  ✓ {message}")
        else:
            print(f"  ✗ {message}")
            failures.append(message)

    with httpx.Client(base_url=args.base_url, timeout=120.0) as client:
        print("[1/5] 健康检查")
        health = client.get("/api/health").json()
        print(f"  kafka={health['kafka']['ok']} parser={health['parser']} embed={health['embed']} store={health['vector_store']} llm={health['llm']}")
        check(health["kafka"]["ok"], "Kafka 可连接")
        system = client.get("/api/system").json()
        print(f"  配置：{json.dumps(system['kafka'], ensure_ascii=False)}")

        print("[2/5] 生成样例 PDF 并批量上传")
        samples = build_samples()
        status_events: list[tuple[str, dict]] = []
        stop_listener = threading.Event()
        listener_ready = threading.Event()
        listener = threading.Thread(
            target=listen_status_events,
            args=(args.base_url, status_events, stop_listener, listener_ready),
            daemon=True,
        )
        listener.start()
        listener_ready.wait(5)
        files = [("files", (path.name, path.read_bytes(), "application/pdf")) for path in samples]
        response = client.post("/api/documents", files=files)
        response.raise_for_status()
        payload = response.json()
        created = payload["documents"]
        print(f"  已创建 {len(created)} 个文档，错误 {len(payload['errors'])} 条")
        check(len(created) == len(samples), f"批量上传返回 {len(samples)} 个文档")
        check(all(item["status"] == "pending" for item in created), "上传后初始状态为 待解析")

        doc_ids = [item["id"] for item in created]
        print("[3/5] 等待 Kafka worker 解析完成（观察 待解析 → 解析中 → 解析完成）")
        transitions = wait_for_completion(client, doc_ids, args.timeout)
        stop_listener.set()
        listener.join(timeout=2)

        for name, payload in status_events:
            document = payload.get("document") if isinstance(payload, dict) else None
            if isinstance(document, dict) and document.get("id") in transitions:
                transitions[document["id"]].add(document.get("status"))

        print(f"  SSE 事件 {len(status_events)} 条，类型：{sorted({name for name, _ in status_events})}")
        for doc_id in doc_ids:
            print(f"  {doc_id[:8]} 状态序列：{sorted(transitions[doc_id])}")
        check(all("completed" in transitions[doc_id] for doc_id in doc_ids), "所有文档解析完成")
        check(
            any("parsing" in transitions[doc_id] for doc_id in doc_ids),
            "至少一篇文档被观察到 解析中 状态",
        )
        check(
            any(name == "document_status" for name, _ in status_events),
            "SSE 实时推送 document_status 事件",
        )

        documents = {item["id"]: item for item in client.get("/api/documents").json()["documents"]}
        for doc_id in doc_ids:
            item = documents[doc_id]
            print(f"  {item['filename']}: {item['chunk_count']} 切片 / {item['image_count']} 图片 / {item['elapsed_ms']} ms")
            check(item["chunk_count"] > 0, f"{item['filename']} 产生切片")

        print("[4/5] 图文混排问答（文本提问，答案里应带原文图片）")
        response = client.post(
            "/api/chat",
            data={"message": "架构图里包含哪些模块？请把相关图片放到答案中。"},
        )
        response.raise_for_status()
        events = parse_sse(response.text)
        meta = next((data for name, data in events if name == "meta"), {})
        answer = "".join(data.get("text", "") for name, data in events if name == "delta")
        print(f"  SSE 事件：{[name for name, _ in events]}")
        print(f"  来源数：{len(meta.get('sources', []))}，图片数：{len(meta.get('images', []))}")
        check(bool(meta.get("sources")), "检索到资料来源")
        check(len(answer.strip()) > 20, "模型返回了答案内容")
        check("![" in answer, "答案中包含 markdown 图片（图文混排）")
        check(
            any("/api/assets/" in url for url in meta.get("images", [])),
            "图片通过 /api/assets 暴露为可访问 URL",
        )
        if meta.get("images"):
            asset = client.get(meta["images"][0])
            check(asset.status_code == 200 and asset.headers.get("content-type", "").startswith("image"),
                  "图片 URL 可直接访问")

        print("[5/5] 图问：上传一张图片和问题一起送多模态模型")
        query_image = SAMPLE_DIR / "query_crop.png"
        Image.open(SAMPLE_DIR / "chart_revenue.png").crop((100, 40, 760, 400)).save(query_image)
        with query_image.open("rb") as handle:
            response = client.post(
                "/api/chat",
                data={"message": "这张图里哪个季度最高？和资料里的结论一致吗？"},
                files={"image": (query_image.name, handle, "image/png")},
            )
        response.raise_for_status()
        events = parse_sse(response.text)
        meta = next((data for name, data in events if name == "meta"), {})
        answer = "".join(data.get("text", "") for name, data in events if name == "delta")
        print(f"  has_query_image={meta.get('has_query_image')} 来源数={len(meta.get('sources', []))}")
        check(meta.get("has_query_image") is True, "后端识别到用户上传的图片")
        check(len(answer.strip()) > 20, "图问返回了答案内容")

        print("[附加] 非 PDF 文件应被拒绝")
        bad = client.post(
            "/api/documents",
            files={"files": ("note.txt", b"hello", "text/plain")},
        )
        check(bad.status_code in {200, 201} and bool(bad.json()["errors"]), "非 PDF 被拒绝并给出错误")

    print()
    if failures:
        print(f"FAILED：{len(failures)} 项未通过")
        for item in failures:
            print(f" - {item}")
        return 1
    print("PASSED：Kafka 上传解析、状态流转、图文混排问答、图问全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
