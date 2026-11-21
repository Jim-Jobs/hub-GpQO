"""共同 API、Redis 与 SSE 契约测试。"""

import unittest
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError

from backend.agent import parse_sentiment_output
from backend.schemas import (
    ChatRequest,
    ChatResult,
    CompletedEvent,
    FailureEvent,
    ProgressEvent,
    RedisSentimentResult,
    Sentiment,
    SentimentAgentOutput,
    WorkflowEvent,
)


class ContractTests(unittest.TestCase):
    """验证外部数据契约的字段边界和事件分派。"""

    def test_chat_request_rejects_empty_text_and_extra_fields(self) -> None:
        """请求文本不能为空，且不接受未定义字段。"""
        with self.assertRaises(ValidationError):
            ChatRequest(text=" ")
        with self.assertRaises(ValidationError):
            ChatRequest(text="你好", user_id="extra")

    def test_redis_sentiment_result_requires_closed_label_set(self) -> None:
        """Redis 情感消息只允许三种已定义标签。"""
        valid = RedisSentimentResult(
            request_id=uuid4(), text="今天很开心", sentiment=Sentiment.POSITIVE, reason="表达开心"
        )
        self.assertEqual(valid.sentiment, Sentiment.POSITIVE)
        with self.assertRaises(ValidationError):
            RedisSentimentResult.model_validate({
                "request_id": str(uuid4()), "text": "好", "sentiment": "happy", "reason": "积极"
            })

    def test_agent_output_rejects_extra_keys_and_unknown_sentiments(self) -> None:
        """Agent 的分类输出严格限制字段和情感标签。"""
        output = SentimentAgentOutput.model_validate_json(
            '{"sentiment":"negative","reason":"表达失落"}'
        )
        self.assertEqual(output.sentiment, Sentiment.NEGATIVE)
        with self.assertRaises(ValidationError):
            SentimentAgentOutput.model_validate_json(
                '{"sentiment":"sad","reason":"表达失落"}'
            )
        with self.assertRaises(ValidationError):
            SentimentAgentOutput.model_validate_json(
                '{"sentiment":"negative","reason":"失落","extra":"ignored"}'
            )

    def test_sentiment_parser_accepts_only_plain_json(self) -> None:
        """识别节点的输出解析不自动修复 Markdown 代码围栏。"""
        output = parse_sentiment_output('{"sentiment":"positive","reason":"表达期待"}')
        self.assertEqual(output.sentiment, Sentiment.POSITIVE)
        with self.assertRaises(ValidationError):
            parse_sentiment_output('```json\n{"sentiment":"positive","reason":"期待"}\n```')

    def test_workflow_event_dispatches_by_event_type(self) -> None:
        """SSE 联合类型按 event_type 选择并校验对应事件结构。"""
        request_id = str(uuid4())
        adapter = TypeAdapter(WorkflowEvent)
        progress = adapter.validate_python({
            "event_type": "progress", "request_id": request_id,
            "stage": "sentiment_processing", "message": "正在识别情感",
        })
        completed = adapter.validate_python({
            "event_type": "completed", "request_id": request_id,
            "result": {
                "request_id": request_id, "sentiment": "positive",
                "reason": "表达开心", "reply": "真好，继续保持。",
            },
        })
        failed = adapter.validate_python({
            "event_type": "failed", "request_id": request_id,
            "failed_stage": "reply_processing", "error_code": "model_error",
            "message": "回复生成失败",
        })
        self.assertIsInstance(progress, ProgressEvent)
        self.assertIsInstance(completed, CompletedEvent)
        self.assertIsInstance(failed, FailureEvent)


if __name__ == "__main__":
    unittest.main()
