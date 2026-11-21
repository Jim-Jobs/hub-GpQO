"""验证 LangGraph 节点输出保留在对应图状态中。"""

import unittest
from unittest.mock import patch

from backend.workflow import build_reply_workflow, build_sentiment_workflow


class WorkflowStateTests(unittest.TestCase):
    """检查状态 schema 声明了各节点写出的结果字段。"""

    def test_sentiment_result_is_kept_in_graph_state(self) -> None:
        """识别图状态应保留节点输出的 sentiment 与 reason。"""
        with patch(
            "backend.workflow.classify_sentiment",
            return_value={"sentiment": "positive", "reason": "语气积极"},
        ):
            result = build_sentiment_workflow().invoke(
                {"request_id": "test-1", "text": "今天心情不错"}
            )

        self.assertEqual(result["sentiment"], "positive")
        self.assertEqual(result["reason"], "语气积极")

    def test_reply_is_kept_in_graph_state(self) -> None:
        """回复图状态应保留节点输出的 reply。"""
        with patch(
            "backend.workflow.generate_reply",
            return_value={"reply": "听起来很不错。"},
        ):
            result = build_reply_workflow().invoke({
                "request_id": "test-2",
                "text": "今天心情不错",
                "sentiment": "positive",
                "reason": "语气积极",
            })

        self.assertEqual(result["reply"], "听起来很不错。")


if __name__ == "__main__":
    unittest.main()
