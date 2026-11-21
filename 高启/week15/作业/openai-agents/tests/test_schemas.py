"""验证前端、Redis 和 SSE 共用数据契约。"""

import unittest
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.schemas import (
    ChatRequest,
    ChatResult,
    CompletedEvent,
    FailureEvent,
    ProgressEvent,
    RedisSentimentResult,
    Sentiment,
    WorkflowEvent,
)


class SchemaTests(unittest.TestCase):
    """覆盖业务契约边界与事件判别。"""

    def test_chat_request_rejects_empty_text(self):
        """空白字符串经去空格后应触发长度校验。"""
        with self.assertRaises(ValidationError):
            ChatRequest(text=" ")

    def test_sentiment_result_rejects_unknown_label(self):
        """Redis 情感结果不接受约定之外的标签。"""
        with self.assertRaises(ValidationError):
            RedisSentimentResult(
                request_id=uuid4(), text="test", sentiment="happy", reason="x"
            )

    def test_sse_union_selects_progress_and_terminal_events(self):
        """SSE 判别联合按事件类型解析并校验最终结果。"""
        adapter = TypeAdapter(WorkflowEvent)
        request_id = str(uuid4())
        progress = adapter.validate_python({
            "event_type": "progress", "request_id": request_id,
            "stage": "sentiment_processing", "message": "识别情感中",
        })
        complete = adapter.validate_python({
            "event_type": "completed", "request_id": request_id,
            "result": {
                "request_id": request_id, "sentiment": "positive",
                "reason": "语气积极", "reply": "继续加油",
            },
        })
        failed = adapter.validate_python({
            "event_type": "failed", "request_id": request_id,
            "failed_stage": "reply_processing", "error_code": "MODEL_ERROR",
            "message": "生成回复失败",
        })
        self.assertIsInstance(progress, ProgressEvent)
        self.assertIsInstance(complete, CompletedEvent)
        self.assertIsInstance(failed, FailureEvent)
        self.assertIsInstance(complete.result, ChatResult)
        self.assertEqual(complete.result.sentiment, Sentiment.POSITIVE)


if __name__ == "__main__":
    unittest.main()
