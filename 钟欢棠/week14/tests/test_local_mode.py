"""无 Docker 预览模式测试：进程内消息总线 + 文件版状态存储。"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import messaging  # noqa: E402
from app.config import Settings, settings  # noqa: E402
from app.store import Store  # noqa: E402


class TestLocalMode(unittest.TestCase):
    def setUp(self):
        self.mode_patch = patch.object(Settings, "local_mode", new_callable=PropertyMock, return_value=True)
        self.mode_patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.original_dir = settings.data_dir
        object.__setattr__(settings, "data_dir", Path(self.tmp.name))

    def tearDown(self):
        object.__setattr__(settings, "data_dir", self.original_dir)
        self.tmp.cleanup()
        self.mode_patch.stop()

    def test_ensure_topics_and_check_broker_are_local_noops(self):
        self.assertTrue(messaging.check_broker())
        messaging.ensure_topics()

    def test_local_bus_fanout_and_group_offsets(self):
        received_a: list = []
        received_b: list = []
        stop = threading.Event()
        threads = [
            threading.Thread(
                target=messaging.consume_loop,
                args=("group-a", ["t.demo"], lambda _topic, payload: received_a.append(payload), stop),
                daemon=True,
            ),
            threading.Thread(
                target=messaging.consume_loop,
                args=("group-b", ["t.demo"], lambda _topic, payload: received_b.append(payload), stop),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        try:
            messaging.send("t.demo", {"n": 1})
            messaging.send("t.demo", {"n": 2})
            for _ in range(60):
                if len(received_a) == 2 and len(received_b) == 2:
                    break
                time.sleep(0.05)
            self.assertEqual([item["n"] for item in received_a], [1, 2])
            self.assertEqual([item["n"] for item in received_b], [1, 2])
        finally:
            stop.set()
            for thread in threads:
                thread.join(timeout=2)

    def test_local_store_persists_docs_qa_and_history(self):
        store = Store()
        self.assertTrue(store.ping())
        store.create_doc("d1", "a.pdf", 123, "待解析")
        store.update_doc("d1", status="解析完成", chunk_count=5)
        self.assertEqual(store.get_doc("d1")["status"], "解析完成")
        self.assertEqual(len(store.list_docs()), 1)

        store.create_qa("q1", "s1", "Kafka 是什么")
        store.append_history("s1", "user", "Kafka 是什么")
        store.apply_answer({
            "request_id": "q1", "session_id": "s1", "status": "done",
            "answer": "回答内容", "sources": [], "images": [], "mode": "demo",
        })
        self.assertEqual(store.get_qa("q1")["status"], "完成")
        self.assertEqual(store.get_qa("q1")["sources"], [])
        self.assertEqual(store.get_history("s1")[-1]["content"], "回答内容")

        store.push_event({"source": "测试", "action": "解析完成", "detail": "", "ts": time.time()})
        self.assertEqual(store.list_events()[0]["action"], "解析完成")
        self.assertTrue((settings.data_dir / "local_state.json").is_file())

        reloaded = Store()
        self.assertEqual(reloaded.get_doc("d1")["chunk_count"], "5")
        self.assertEqual(reloaded.get_qa("q1")["answer"], "回答内容")


if __name__ == "__main__":
    unittest.main()
