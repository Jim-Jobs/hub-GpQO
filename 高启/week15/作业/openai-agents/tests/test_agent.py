"""验证情感 Agent 输出的结构校验。"""

import unittest

from pydantic import ValidationError

from backend.agent import parse_sentiment_output


class AgentOutputTests(unittest.TestCase):
    """确保模型自由文本不能绕过结果格式校验。"""

    def test_parses_allowed_sentiment_json(self):
        """合法情感 JSON 映射为结构化输出模型。"""
        result = parse_sentiment_output(
            '{"sentiment":"negative","reason":"表达了失望"}'
        )
        self.assertEqual(result.sentiment, "negative")

    def test_rejects_unknown_sentiment_label(self):
        """未知情感标签不能被静默转换为合法标签。"""
        with self.assertRaises(ValidationError):
            parse_sentiment_output(
                '{"sentiment":"sad","reason":"不在类别中"}'
            )


if __name__ == "__main__":
    unittest.main()
