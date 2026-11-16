"""离线 Worker 测试：验证 Kafka 任务 -> 解析/状态更新、问答 -> 回答消息。"""

from __future__ import annotations

import logging
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import worker  # noqa: E402
from app.config import STATUS_DONE, STATUS_FAILED, STATUS_PARSING, STATUS_PENDING, settings  # noqa: E402


class MiniStore:
    def __init__(self) -> None:
        self.docs: dict = {}
        self.qa: dict = {}
        self.history: list = []

    def get_doc(self, doc_id):
        return self.docs.get(doc_id)

    def update_doc(self, doc_id, **fields):
        self.docs.setdefault(doc_id, {}).update(fields)

    def list_docs(self):
        return [{"id": key, "filename": value.get("filename", "x.pdf")} for key, value in self.docs.items()]

    def create_qa(self, request_id, session_id, question):
        self.qa[request_id] = {"status": "排队中"}

    def update_qa(self, request_id, **fields):
        self.qa.setdefault(request_id, {}).update(fields)

    def get_history(self, session_id):
        return self.history

    def apply_answer(self, payload):
        self.qa[payload["request_id"]]["answer"] = payload["answer"]


class TestWorkerOffline(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)  # 失败路径会打印预期内的异常栈，测试时静音
        self.store = MiniStore()
        self.sent: list = []
        self.pdf = settings.data_dir / "uploads" / "worker_unit.pdf"
        self.pdf.parent.mkdir(parents=True, exist_ok=True)
        self.pdf.write_bytes(b"%PDF-1.4 worker unit test")

        def fake_send(topic, payload, key=None):
            self.sent.append((topic, payload, key))

        self.patches = [
            patch.object(worker, "get_store", lambda: self.store),
            patch.object(worker.messaging, "send", fake_send),
            patch.object(worker.messaging, "publish_event", lambda *a, **k: None),
            patch.object(worker.rag, "add_document", return_value=3),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        logging.disable(logging.NOTSET)
        try:
            self.pdf.unlink()
        except OSError:
            pass

    def test_parse_task_transitions_and_status_topic(self):
        self.store.docs["d1"] = {"filename": "worker_unit.pdf", "status": STATUS_PENDING}
        parsed = {
            "markdown": "## 第 1 页\n\nKafka 传递消息。\n\n![图](/media/docs/d1/assets/p.png)",
            "assets": ["/media/docs/d1/assets/p.png"],
            "pages": 1,
            "backend": "mineru",
            "attempts": [],
        }
        with patch.object(worker.parser, "parse_pdf", return_value=parsed):
            worker.dispatch(
                settings.topics["parse_tasks"],
                {"doc_id": "d1", "filename": "worker_unit.pdf", "pdf_path": "uploads/worker_unit.pdf"},
            )
        doc = self.store.docs["d1"]
        self.assertEqual(doc["status"], STATUS_DONE)
        self.assertEqual(doc["chunk_count"], 3)
        self.assertEqual(doc["image_count"], 1)
        self.assertEqual(doc["parser"], "mineru")
        statuses = [payload["status"] for topic, payload, _ in self.sent if topic.endswith("pdf.status")]
        self.assertEqual(statuses, [STATUS_PARSING, STATUS_DONE])

    def test_parse_failure_reports_status(self):
        self.store.docs["d2"] = {"filename": "worker_unit.pdf", "status": STATUS_PENDING}
        with patch.object(worker.parser, "parse_pdf", side_effect=RuntimeError("解析炸了")):
            worker.dispatch(
                settings.topics["parse_tasks"],
                {"doc_id": "d2", "filename": "worker_unit.pdf", "pdf_path": "uploads/worker_unit.pdf"},
            )
        self.assertEqual(self.store.docs["d2"]["status"], STATUS_FAILED)
        self.assertIn("解析炸了", self.store.docs["d2"]["error"])

    def test_qa_task_publishes_answer_to_kafka(self):
        self.store.docs["d1"] = {"filename": "worker_unit.pdf", "status": STATUS_DONE}
        self.store.qa["q1"] = {"status": "排队中"}
        ranked = [({"doc_id": "d1", "filename": "worker_unit.pdf", "page": 1, "text": "Kafka 传递消息", "images": []}, 0.9)]
        expected = {
            "answer": "Kafka 负责传递消息 ![图](/media/docs/d1/assets/p.png)",
            "mode": "多模态 LLM",
            "sources": [{"index": 1, "doc_id": "d1", "filename": "worker_unit.pdf", "page": 1}],
            "images": ["/media/docs/d1/assets/p.png"],
        }
        with patch.object(worker.rag, "retrieve", return_value=ranked), \
             patch.object(worker.llm, "answer", return_value=expected) as answer_mock:
            worker.dispatch(
                settings.topics["qa_requests"],
                {"request_id": "q1", "session_id": "s1", "question": "Kafka 做什么",
                 "doc_ids": ["d1"], "image_paths": [], "use_vision": True},
            )
        self.assertTrue(answer_mock.called)
        self.assertEqual(answer_mock.call_args.kwargs["use_vision"], True)
        responses = [payload for topic, payload, _ in self.sent if topic.endswith("qa.responses")]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["status"], "done")
        self.assertIn("![图]", responses[0]["answer"])
        self.assertEqual(responses[0]["sources"], expected["sources"])


if __name__ == "__main__":
    unittest.main()
