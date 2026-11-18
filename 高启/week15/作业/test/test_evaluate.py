"""评测指标的单元测试。"""

import unittest

from test.evaluate import calculate_classification_metrics, summarize_records
from test.schemas import EvaluationRecord, SentimentLabel, TestCase


class EvaluationTests(unittest.TestCase):
    """验证分类指标、格式通过率与人工评分汇总。"""

    def test_macro_f1_uses_all_three_labels(self) -> None:
        """Macro-F1 对三个类别分别计算后取平均。"""
        result = calculate_classification_metrics(
            ["positive", "negative", "neutral", "neutral"],
            ["positive", "positive", "neutral", "negative"],
        )
        self.assertAlmostEqual(result["macro_f1"], (2 / 3 + 0 + 2 / 3) / 3)

    def test_summary_matches_cases_by_id(self) -> None:
        """评测结果按 case_id 对齐标准标签并汇总质量指标。"""
        cases = [
            TestCase(case_id="C001", text="很开心", expected_sentiment=SentimentLabel.POSITIVE),
            TestCase(case_id="C002", text="很难过", expected_sentiment=SentimentLabel.NEGATIVE),
        ]
        records = [
            EvaluationRecord(
                case_id="C001", run_id="run-a", framework="langgraph", status="completed",
                predicted_sentiment=SentimentLabel.POSITIVE, format_valid=True, latency_ms=100,
                input_tokens=10, output_tokens=5, reply="真好，继续加油。",
                human_scores={"empathy": 4.0},
            ),
            EvaluationRecord(
                case_id="C002", run_id="run-a", framework="langgraph", status="completed",
                predicted_sentiment=SentimentLabel.NEUTRAL, format_valid=False, latency_ms=300,
                input_tokens=12, output_tokens=7, reply="我听到了。",
                human_scores={"empathy": 3.0},
            ),
        ]
        result = summarize_records(cases, records)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["accuracy"], 0.5)
        self.assertEqual(result["format_validity_rate"], 0.5)
        self.assertEqual(result["human_score_means"]["empathy"], 3.5)
        self.assertEqual(result["latency_ms_mean"], 200)

    def test_summary_reports_empty_values_when_no_completed_records(self) -> None:
        """没有有效记录时返回空指标，不以零冒充测量结果。"""
        cases = [TestCase(case_id="C001", text="样例", expected_sentiment=SentimentLabel.NEUTRAL)]
        records = [
            EvaluationRecord(
                case_id="C001", run_id="run-a", framework="openai-agents", status="failed",
                latency_ms=50, error="模型不可用",
            )
        ]
        result = summarize_records(cases, records)
        self.assertIsNone(result["accuracy"])
        self.assertIsNone(result["format_validity_rate"])
        self.assertEqual(result["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
