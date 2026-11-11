"""真实环境冒烟验收：健康检查 → 上传 → 状态 → 文本问答 → 图文问答 → 去重。

用法：
    python scripts/smoke_acceptance.py --pdf data/uploads/a.pdf --pdf data/uploads/b.pdf
    python scripts/smoke_acceptance.py --pdf a.pdf --kb acceptance-20261110
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"DONE", "FAILED"}
ACTIVE = {"PENDING", "PARSING"}


class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, bool, str]] = []

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append((name, ok, detail))
        mark = "PASS" if ok else "FAIL"
        print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    def ok(self) -> bool:
        return all(item[1] for item in self.items)

    def summary(self) -> None:
        passed = sum(1 for item in self.items if item[1])
        print(f"\n{passed}/{len(self.items)} checks passed")
        for name, ok, detail in self.items:
            if not ok:
                print(f"  - FAILED: {name} {detail}")


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key} if api_key else {}


def _upload(client: httpx.Client, kb: str, paths: list[Path]) -> dict:
    files = [
        ("files", (path.name, path.read_bytes(), "application/pdf"))
        for path in paths
    ]
    response = client.post(
        "/upload/documents",
        data={"knowledge_base_id": kb},
        files=files,
    )
    response.raise_for_status()
    return response.json()


def _poll(
    client: httpx.Client, kb: str, file_ids: list[int], timeout: float, interval: float
) -> dict[int, dict]:
    deadline = time.monotonic() + timeout
    last: dict[int, dict] = {}
    while time.monotonic() < deadline:
        response = client.get(
            "/documents/status", params={"file_ids": ",".join(map(str, file_ids))}
        )
        response.raise_for_status()
        last = {
            item["file_id"]: item for item in response.json().get("items", [])
        }
        if last and all(item["status"] in TERMINAL for item in last.values()):
            return last
        time.sleep(interval)
    return last


def _detail(client: httpx.Client, kb: str) -> dict[int, dict]:
    response = client.get(
        "/documents", params={"knowledge_base_id": kb, "limit": 200}
    )
    response.raise_for_status()
    return {item["file_id"]: item for item in response.json().get("items", [])}


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (96, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Real-environment acceptance smoke")
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="API base URL"
    )
    parser.add_argument("--kb", default="acceptance", help="knowledge base id")
    parser.add_argument("--api-key", default="")
    parser.add_argument(
        "--pdf",
        action="append",
        default=[],
        help="PDF path, repeat 2-3 times",
    )
    parser.add_argument("--timeout", type=float, default=900, help="parse timeout sec")
    parser.add_argument("--poll", type=float, default=2.0)
    parser.add_argument("--skip-chat", action="store_true")
    args = parser.parse_args()

    pdfs = [Path(item) for item in args.pdf]
    if not pdfs:
        print("need at least one --pdf (prefer 2-3 PDFs with text/charts)")
        return 2
    missing = [path for path in pdfs if not path.is_file()]
    if missing:
        print(f"pdf not found: {', '.join(str(path) for path in missing)}")
        return 2

    report = Report()
    client = httpx.Client(
        base_url=args.base_url.rstrip("/"),
        headers=_headers(args.api_key),
        timeout=120,
    )

    try:
        print(f"API: {args.base_url}  KB: {args.kb}")
        health = client.get("/health")
        report.check("GET /health 200", health.status_code == 200)
        body = health.json()
        report.check(
            "/health status ok",
            body.get("status") == "ok",
            f"mock={body.get('mock')} parse={body.get('parse_backend')}",
        )

        deps = client.get("/health/deps")
        report.check("GET /health/deps 200", deps.status_code == 200)
        dep_body = deps.json()
        failed = [
            name
            for name, item in dep_body.get("deps", {}).items()
            if not item.get("ok")
        ]
        report.check("all dependencies ok", not failed, f"failed={failed}")

        upload = _upload(client, args.kb, pdfs)
        report.check(
            "upload accepted",
            upload.get("accepted", 0) >= 1,
            f"accepted={upload.get('accepted')} rejected={upload.get('rejected')}",
        )
        file_ids = [
            item["file_id"]
            for item in upload.get("items", [])
            if item.get("file_id") and item.get("status") != "DUPLICATE"
        ]
        report.check("file ids returned", bool(file_ids), f"ids={file_ids}")
        if not file_ids:
            report.summary()
            return 1

        print(f"waiting for parse (timeout={args.timeout}s)...")
        statuses = _poll(client, args.kb, file_ids, args.timeout, args.poll)
        for file_id in file_ids:
            status = statuses.get(file_id, {})
            report.check(
                f"file {file_id} DONE",
                status.get("status") == "DONE",
                f"status={status.get('status')}",
            )

        details = _detail(client, args.kb)
        for file_id in file_ids:
            item = details.get(file_id, {})
            report.check(
                f"file {file_id} indexed",
                (item.get("page_count") or 0) > 0
                and (item.get("chunk_count") or 0) > 0,
                f"pages={item.get('page_count')} chunks={item.get('chunk_count')}",
            )

        if not args.skip_chat:
            chat = client.post(
                "/chat",
                json={
                    "question": "请概括文档的主要内容，并标注来源。",
                    "knowledge_base_id": args.kb,
                },
            )
            chat.raise_for_status()
            chat_body = chat.json()
            report.check(
                "text chat answered",
                bool(chat_body.get("answer")),
                f"sources={len(chat_body.get('sources', []))}",
            )
            report.check(
                "text chat has sources",
                bool(chat_body.get("sources")),
            )

            image_response = client.post(
                "/chat",
                data={
                    "question": "这张图片里有什么？",
                    "knowledge_base_id": args.kb,
                },
                files=[
                    ("images", ("acceptance.png", _image_bytes(), "image/png"))
                ],
            )
            image_response.raise_for_status()
            image_body = image_response.json()
            report.check(
                "image chat used user image",
                bool(image_body.get("used_images")),
                f"used={len(image_body.get('used_images', []))}",
            )

        duplicate = _upload(client, args.kb, [pdfs[0]])
        report.check(
            "duplicate upload detected",
            duplicate.get("duplicates", 0) >= 1,
            f"duplicates={duplicate.get('duplicates')}",
        )

    except Exception as exc:
        report.check("unexpected error", False, str(exc))
    finally:
        client.close()

    report.summary()
    return 0 if report.ok() else 1


if __name__ == "__main__":
    raise SystemExit(main())
