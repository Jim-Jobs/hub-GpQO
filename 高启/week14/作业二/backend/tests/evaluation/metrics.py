"""确定性计算评分总分、比例指标和黄金集整体通过状态。"""

from backend.tests.evaluation.schemas import (
    DimensionScores,
    EvaluationCaseResult,
    MetricResult,
)


DIMENSION_WEIGHTS = {
    "correctness": 35,
    "grounding": 35,
    "completeness": 20,
    "behavior": 10,
}
CORE_CATEGORIES = {"FQA", "DOC-QA", "TableQA", "KBQA", "TaskQA"}


def weighted_score(scores: DimensionScores) -> float | None:
    """按适用维度归一到百分制，避免把不适用项当作零分。"""
    weighted_total = 0
    applicable_weight = 0
    for name, weight in DIMENSION_WEIGHTS.items():
        score = getattr(scores, name)
        if score is None:
            continue
        weighted_total += score * weight
        applicable_weight += weight
    if applicable_weight == 0:
        return None
    return round(weighted_total / (5 * applicable_weight) * 100, 2)


def scoring_passed(scores: DimensionScores, fatal_flags: list[str]) -> bool:
    """应用 80 分门槛、关键维度至少 4 分和 fatal flag 硬失败规则。"""
    total = weighted_score(scores)
    if total is None or total < 80 or fatal_flags:
        return False
    return all(
        score is None or score >= 4
        for score in (scores.correctness, scores.grounding)
    )


def ratio(values: list[bool | None]) -> MetricResult:
    """忽略不适用项，返回比例的可追溯分子和分母。"""
    applicable = [value for value in values if value is not None]
    numerator = sum(applicable)
    denominator = len(applicable)
    return MetricResult(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 4) if denominator else None,
    )


def compute_metrics(
    results: list[EvaluationCaseResult],
) -> tuple[dict[str, MetricResult], dict[str, MetricResult]]:
    """汇总路由、Top-5、来源、阶段、图像和各类别通过比例。"""
    def values_for(name: str) -> list[bool | None]:
        """按名称收集所有用例的某项确定性结果。"""
        return [result.deterministic_checks.get(name) for result in results]

    core = [result for result in results if result.category in CORE_CATEGORIES]
    out_of_scope = [
        result
        for result in results
        if result.trace.get("expected_behavior") == "answer_and_guide"
    ]
    abstentions = [
        result
        for result in results
        if result.trace.get("expected_behavior") == "abstain"
    ]
    multimodal = [
        result
        for result in results
        if result.category in {"multimodal_input", "multimodal_output"}
    ]
    categories = sorted({result.category for result in results})
    category_rates = {
        category: ratio(
            [result.passed for result in results if result.category == category]
        )
        for category in categories
    }
    metrics = {
        "case_score_pass_rate": ratio([result.score_passed for result in results]),
        "route_accuracy": ratio(values_for("route")),
        "hit_at_5": ratio(values_for("hit_at_5")),
        "citation_page_accuracy": ratio(values_for("citations")),
        "retrieval_behavior_accuracy": ratio(values_for("retrieval_behavior")),
        "status_transition_accuracy": ratio(values_for("status_transitions")),
        "response_route_accuracy": ratio(values_for("response_route")),
        "final_status_accuracy": ratio(values_for("final_status")),
        "mixed_media_response_accuracy": ratio(values_for("mixed_media_shape")),
        "stage_sequence_accuracy": ratio(values_for("stages")),
        "user_image_delivery_accuracy": ratio(values_for("user_image_delivery")),
        "image_asset_ownership_accuracy": ratio(values_for("asset_ownership")),
        "out_of_scope_deterministic_pass_rate": ratio(
            [result.deterministic_checks.get("deterministic_all") for result in out_of_scope]
        ),
        "evidence_abstention_pass_rate": ratio(
            [result.deterministic_checks.get("deterministic_all") for result in abstentions]
        ),
        "multimodal_deterministic_pass_rate": ratio(
            [result.deterministic_checks.get("deterministic_all") for result in multimodal]
        ),
        "core_pass_rate": ratio([result.passed for result in core]),
    }
    return metrics, category_rates


def report_passed(
    results: list[EvaluationCaseResult],
    category_rates: dict[str, MetricResult],
) -> bool:
    """按核心问答比例、硬性行为断言、图文断言和评分错误决定整轮通过。"""
    core = [result for result in results if result.category in CORE_CATEGORIES]
    if len(core) != 20 or sum(result.passed for result in core) < 18:
        return False
    if any(
        category_rates.get(category) is None
        or category_rates[category].numerator < 3
        or category_rates[category].denominator != 4
        for category in CORE_CATEGORIES
    ):
        return False

    out_of_scope = [
        result
        for result in results
        if result.category == "scope"
        and result.trace.get("expected_behavior") == "answer_and_guide"
    ]
    abstentions = [
        result
        for result in results
        if result.category == "scope"
        and result.trace.get("expected_behavior") == "abstain"
    ]
    multimodal = [
        result
        for result in results
        if result.category in {"multimodal_input", "multimodal_output"}
    ]
    if len(out_of_scope) != 5 or not all(_checks_pass(result) for result in out_of_scope):
        return False
    if len(abstentions) != 5 or not all(_checks_pass(result) for result in abstentions):
        return False
    if len(multimodal) != 3 or not all(_checks_pass(result) for result in multimodal):
        return False
    return all(
        result.error is None
        and result.scoring is not None
        and not result.scoring.fatal_flags
        for result in results
    )


def _checks_pass(result: EvaluationCaseResult) -> bool:
    """要求目标分支的所有适用确定性断言均为真。"""
    applicable = [
        value
        for value in result.deterministic_checks.values()
        if value is not None
    ]
    return bool(applicable) and all(applicable)
