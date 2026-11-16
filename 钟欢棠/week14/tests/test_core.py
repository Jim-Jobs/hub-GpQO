"""离线单元测试：不需要 Redis / Kafka / LLM 即可运行。

    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm, parser, rag  # noqa: E402
from app.config import settings  # noqa: E402


class TestChunking(unittest.TestCase):
    def test_chunks_keep_pages_and_images(self):
        markdown = (
            "## 第 1 页\n\n" + "Kafka 是基于发布订阅的消息队列，用于解耦生产者与消费者。" * 30
            + "\n\n![架构图](/media/docs/d1/assets/arch.png)\n\n"
            + "## 第 2 页\n\nRedis 用 List 存储历史对话，用 EXPIRE 控制会话过期。\n"
        )
        chunks = rag.build_chunks(markdown, "d1", "demo.pdf")
        self.assertGreater(len(chunks), 1)
        self.assertEqual(len({c["id"] for c in chunks}), len(chunks))
        self.assertTrue(any(c["images"] for c in chunks), "图片引用应保留在分块里")
        self.assertTrue(any(c["page"] == 2 for c in chunks), "页码应随分块保留")

    def test_keyword_score_prefers_relevant_text(self):
        query = "Kafka 消息队列如何传递消息"
        relevant = "Kafka 的消息队列通过 topic 在生产者与消费者之间传递消息"
        irrelevant = "Redis 是一种缓存数据库，支持字符串和哈希结构"
        self.assertGreater(rag.keyword_score(query, relevant), rag.keyword_score(query, irrelevant))


class TestParser(unittest.TestCase):
    def test_rewrite_images(self):
        mapping = {
            "path:images/arch.png": "/media/docs/d1/assets/arch.png",
            "name:arch.png": "/media/docs/d1/assets/arch.png",
        }
        markdown = "文本\n\n![架构图](images/arch.png)\n\n<img src=\"images/arch.png\">"
        result = parser._rewrite_images(markdown, mapping, ["/media/docs/d1/assets/arch.png"])
        self.assertEqual(result.count("/media/docs/d1/assets/arch.png"), 2)
        self.assertNotIn("](images/arch.png)", result)

    def test_append_gallery_when_markdown_has_no_image(self):
        url = "/media/docs/d1/assets/page_001.png"
        result = parser._rewrite_images("纯文本内容", {}, [url])
        self.assertIn(f"![文档图片 1]({url})", result)


class TestAnswerFallback(unittest.TestCase):
    def test_demo_answer_is_labeled_and_contains_image(self):
        url = "/media/docs/d1/assets/arch.png"
        ranked = [({"filename": "demo.pdf", "page": 1, "text": "Kafka 传递消息", "images": [url]}, 0.9)]
        answer = llm._demo_answer("Kafka 的作用？", ranked, [url])
        self.assertIn("演示模式", answer)
        self.assertIn(f"![检索到的相关图片 1]({url})", answer)

    def test_media_url_round_trip(self):
        relative = "docs/d1/assets/arch.png"
        url = settings.media_url(relative)
        self.assertEqual(url, f"/media/{relative}")
        self.assertEqual(settings.media_path(url), settings.data_dir / relative)


if __name__ == "__main__":
    unittest.main()
