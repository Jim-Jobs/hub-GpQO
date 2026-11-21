"""使用标准库汇总两种实现的分类、格式、回复评分和性能指标。"""

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

from test.schemas import EvaluationRecord, SentimentLabel, TestCase

LABELS = tuple(label.value for label in SentimentLabel)


def calculate_classification_metrics(
    expected: Sequence[str], predicted: Sequence[str]
) -> dict[str, Any]:
    """计算三分类的 Accuracy、Macro-F1、分类明细和混淆矩阵。

    Args:
        expected: 人工标注的真实标签序列。
        predicted: 模型预测标签序列，顺序与 `expected` 对应。

    Returns:
        包含 accuracy、macro_f1、每类 precision/recall/F1 和混淆矩阵的字典。

    Raises:
        ValueError: 两个序列长度不同，或标签不属于项目定义的三类。
    """
    if len(expected) != len(predicted):
        raise ValueError("真实标签和预测标签数量必须一致")
    if any(label not in LABELS for label in (*expected, *predicted)):
        raise ValueError("标签必须是 positive、negative、neutral 之一")
    if not expected:
        return {"accuracy": None, "macro_f1": None, "per_label": {}, "confusion_matrix": {}}

    matrix = {actual: {guess: 0 for guess in LABELS} for actual in LABELS}
    for actual, guess in zip(expected, predicted):
        matrix[actual][guess] += 1

    per_label: dict[str, dict[str, float]] = {}
    f1_values: list[float] = []
    for label in LABELS:
        true_positive = matrix[label][label]
        false_positive = sum(matrix[other][label] for other in LABELS if other != label)
        false_negative = sum(matrix[label][other] for other in LABELS if other != label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_label[label] = {"precision": precision, "recall": recall, "f1": f1}
        f1_values.append(f1)

    correct = sum(actual == guess for actual, guess in zip(expected, predicted))
    return {
        "accuracy": correct / len(expected),
        "macro_f1": sum(f1_values) / len(f1_values),
        "per_label": per_label,
        "confusion_matrix": matrix,
    }


def _percentile(values: Sequence[float], quantile: float) -> float | None:
    """按 nearest-rank 方法计算给定分位数。

    Args:
        values: 已测量的数值序列。
        quantile: 0 到 1 之间的分位点。

    Returns:
        对应分位数；输入为空时返回 None。

    Raises:
        ValueError: 分位点不在 0 到 1 之间。
    """
    if not 0 <= quantile <= 1:
        raise ValueError("quantile 必须在 0 到 1 之间")
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def summarize_records(
    cases: Sequence[TestCase], records: Sequence[EvaluationRecord]
) -> dict[str, Any]:
    """按 case ID 汇总分类、格式、人工评分、耗时和 Token 指标。

    Args:
        cases: 人工标注的测试样例。
        records: 某次框架运行生成的记录。

    Returns:
        可序列化的汇总字典；缺少有效观测时相应指标为 None。

    Raises:
        ValueError: 运行记录引用了测试集不存在的 case ID。
    """
    case_by_id = {case.case_id: case for case in cases}
    unknown_ids = {record.case_id for record in records} - case_by_id.keys()
    if unknown_ids:
        raise ValueError(f"运行记录引用了未知 case_id: {', '.join(sorted(unknown_ids))}")

    completed = [record for record in records if record.status == "completed"]
    classified = [record for record in completed if record.predicted_sentiment is not None]
    classification = calculate_classification_metrics(
        [case_by_id[record.case_id].expected_sentiment.value for record in classified],
        [record.predicted_sentiment.value for record in classified if record.predicted_sentiment],
    )

    format_values = [record.format_valid for record in completed if record.format_valid is not None]
    latencies = [record.latency_ms for record in records]
    score_values: dict[str, list[float]] = {}
    for record in completed:
        for dimension, score in record.human_scores.items():
            score_values.setdefault(dimension, []).append(score)

    input_tokens = [record.input_tokens for record in records if record.input_tokens is not None]
    output_tokens = [record.output_tokens for record in records if record.output_tokens is not None]
    return {
        "sample_count": len(classified),
        **classification,
        "format_validity_rate": sum(format_values) / len(format_values) if format_values else None,
        "human_score_means": {
            dimension: sum(scores) / len(scores) for dimension, scores in sorted(score_values.items())
        },
        "latency_ms_mean": sum(latencies) / len(latencies) if latencies else None,
        "latency_ms_p50": _percentile(latencies, 0.50),
        "latency_ms_p95": _percentile(latencies, 0.95),
        "input_tokens_total": sum(input_tokens),
        "output_tokens_total": sum(output_tokens),
        "failed_count": sum(record.status == "failed" for record in records),
    }


def _read_jsonl(path: Path, model_type: type[TestCase] | type[EvaluationRecord]) -> list[Any]:
    """读取并校验逐行 JSON 数据文件。

    Args:
        path: JSONL 文件路径。
        model_type: 用于校验每条记录的 Pydantic 模型类型。

    Returns:
        按文件顺序解析的模型对象列表。

    Raises:
        FileNotFoundError: 文件不存在。
        ValueError: 任意一行不符合指定模型结构。
    """
    return [model_type.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    """解析评测文件路径并打印 JSON 汇总结果。

    Args:
        无；命令行参数指定样例文件和运行结果文件。

    Returns:
        汇总结果打印完成后无返回值。

    Raises:
        SystemExit: 必需参数缺失时由参数解析器退出。
        Exception: 文件读取或数据校验失败。
    """
    parser = argparse.ArgumentParser(description="汇总情感 Agent 评测记录")
    parser.add_argument("--cases", type=Path, default=Path(__file__).parent / "data" / "cases.jsonl")
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    cases = _read_jsonl(args.cases, TestCase)
    records = _read_jsonl(args.results, EvaluationRecord)
    print(json.dumps(summarize_records(cases, records), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
