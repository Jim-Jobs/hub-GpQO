"""离线接口测试：用内存 Store + 假 Kafka 验证上传/状态/问答路由（无需 Redis/Kafka/LLM）。"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402
from app.config import settings  # noqa: E402


class FakeStore:
    def __init__(self) -> None:
        self.docs: dict = {}
        self.qa: dict = {}
        self.history: dict = {}
        self.events: list = []

    def ping(self) -> bool:
        return True

    def create_doc(self, doc_id, filename, size, status):
        doc = {"id": doc_id, "filename": filename, "size": str(size), "status": status,
               "created_at": str(time.time()), "chunk_count": "0", "image_count": "0",
               "pages": "0", "parser": "", "error": ""}
        self.docs[doc_id] = doc
        return doc

    def update_doc(self, doc_id, **fields):
        self.docs.setdefault(doc_id, {}).update({k: str(v) for k, v in fields.items()})

    def get_doc(self, doc_id):
        return self.docs.get(doc_id)

    def list_docs(self):
        return list(self.docs.values())

    def create_qa(self, request_id, session_id, question):
        state = {"id": request_id, "session_id": session_id, "question": question,
                 "status": "排队中", "answer": "", "sources": [], "images": [], "mode": "", "error": ""}
        self.qa[request_id] = state
        return state

    def update_qa(self, request_id, **fields):
        self.qa.setdefault(request_id, {}).update(fields)

    def get_qa(self, request_id):
        return self.qa.get(request_id)

    def apply_answer(self, payload):
        self.update_qa(payload["request_id"], status="完成", answer=payload["answer"])
        self.append_history(payload["session_id"], "assistant", payload["answer"])

    def append_history(self, session_id, role, content, images=None):
        self.history.setdefault(session_id, []).append({"role": role, "content": content, "images": images or []})

    def get_history(self, session_id):
        return self.history.get(session_id, [])

    def clear_history(self, session_id):
        self.history.pop(session_id, None)

    def push_event(self, event):
        self.events.insert(0, event)

    def list_events(self, limit=30):
        return self.events[:limit]


class TestApiOffline(unittest.TestCase):
    def setUp(self):
        self.fake = FakeStore()
        self.sent = []

        def fake_send(topic, payload, key=None):
            self.sent.append((topic, payload, key))

        self.patches = [
            patch.object(main, "get_store", lambda: self.fake),
            patch.object(main.messaging, "send", fake_send),
            patch.object(main.messaging, "publish_event", lambda *a, **k: None),
            patch.object(main.messaging, "check_broker", lambda *a, **k: True),
        ]
        for item in self.patches:
            item.start()
        (settings.data_dir / "uploads").mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "questions").mkdir(parents=True, exist_ok=True)
        self.client = TestClient(main.app)
        self.created_files: list[Path] = []

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        for path in self.created_files:
            try:
                path.unlink()
            except OSError:
                pass

    def test_upload_multiple_pdfs_creates_kafka_tasks_and_pending_status(self):
        files = [
            ("files", ("a.pdf", b"%PDF-1.4 aaa", "application/pdf")),
            ("files", ("b.pdf", b"%PDF-1.4 bbb", "application/pdf")),
        ]
        response = self.client.post("/api/upload", files=files)
        self.assertEqual(response.status_code, 200)
        documents = response.json()["documents"]
        self.assertEqual(len(documents), 2)
        self.assertTrue(all(doc["status"] == "待解析" for doc in documents))

        tasks = [(topic, payload) for topic, payload, _ in self.sent if topic.endswith("pdf.tasks")]
        self.assertEqual(len(tasks), 2)
        for _topic, payload in tasks:
            self.created_files.append(settings.data_dir / payload["pdf_path"])
            self.assertTrue((settings.data_dir / payload["pdf_path"]).is_file())
        self.assertEqual(len(self.client.get("/api/documents").json()["documents"]), 2)

    def test_qa_with_image_and_question(self):
        response = self.client.post(
            "/api/qa",
            data={"question": "这张图讲了什么？", "session_id": "s1", "doc_ids": "", "use_vision": "true"},
            files={"image": ("question.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "排队中")

        requests = [payload for topic, payload, _ in self.sent if topic.endswith("qa.requests")]
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0]["image_paths"])
        self.assertTrue(requests[0]["use_vision"])
        self.created_files.append(settings.data_dir / requests[0]["image_paths"][0])
        self.assertEqual(self.fake.history["s1"][0]["role"], "user")
        self.assertEqual(self.client.get(f"/api/qa/{body['request_id']}").json()["status"], "排队中")

    def test_qa_text_only_with_vision_disabled(self):
        response = self.client.post(
            "/api/qa",
            data={"question": "Kafka 的作用", "session_id": "s2", "use_vision": "false"},
        )
        self.assertEqual(response.status_code, 200)
        payload = [p for topic, p, _ in self.sent if topic.endswith("qa.requests")][0]
        self.assertEqual(payload["image_paths"], [])
        self.assertFalse(payload["use_vision"])

    def test_status_and_event_endpoints(self):
        self.fake.push_event({"source": "测试", "action": "解析完成", "detail": "ok", "ts": time.time()})
        self.fake.update_doc("d1", status="解析完成", chunk_count=3)
        events = self.client.get("/api/events").json()["events"]
        self.assertEqual(events[0]["action"], "解析完成")
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/history?session_id=s3").json()["messages"], [])
        self.assertEqual(self.client.delete("/api/history?session_id=s3").status_code, 200)
        self.assertEqual(self.client.get("/api/config").json()["topics"]["qa_requests"], "mmrag.qa.requests")


if __name__ == "__main__":
    unittest.main()
