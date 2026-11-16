"""使用 Qwen3-VL 对黄金问答的自然语言质量进行独立评分。"""

import json
from importlib.resources import files

from backend.app.agents.chat.models import ChatModelClient
from backend.app.chat.domain.models import ChatImage, ChatResponse
from backend.tests.evaluation.schemas import EvaluationCase, ScoringResult


PROMPT_VERSION = "scoring-v1"


class ScoringOutputError(ValueError):
    """评分模型返回内容无法解析或不符合黄金集评分契约。"""


class ScoringAgent:
    """将标准化用例、实际回答和来源证据交给评分模型。"""

    def __init__(self, model: ChatModelClient) -> None:
        """保存共享 Qwen3-VL 客户端并读取版本化评分提示词。"""
        self._model = model
        self.prompt = files("backend.tests.evaluation.prompts").joinpath(
            "scoring.md"
        ).read_text(encoding="utf-8")

    def score_answer(
        self,
        case: EvaluationCase,
        response: ChatResponse,
        evidence: list[dict],
        *,
        input_image: ChatImage | None = None,
    ) -> ScoringResult:
        """按评分 rubric 评估回答，图文用例会把原始用户图附给评分模型。"""
        scoring_input = {
            "case_id": case.id,
            "question": case.question,
            "expected_behavior": case.expected_behavior,
            "required_answer_points": case.required_answer_points,
            "expected_evidence_pages": case.evidence_pages,
            "answer_route": response.route,
            "answer_segments": response.answer_segments,
            "citations": response.citations,
            "evidence": evidence,
        }
        user_prompt = json.dumps(scoring_input, ensure_ascii=False, separators=(",", ":"))
        try:
            raw_result = self._model.complete(
                self.prompt,
                user_prompt,
                [input_image] if input_image is not None else [],
            )
            result = ScoringResult.model_validate(json.loads(raw_result))
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise ScoringOutputError("评分模型输出不是有效的 ScoringResult JSON") from error
        if result.case_id != case.id:
            raise ScoringOutputError("评分模型返回了不同的 case_id")
        valid_evidence_ids = {
            item.get("chunk_id") for item in evidence if isinstance(item.get("chunk_id"), str)
        }
        if not set(result.evidence_refs).issubset(valid_evidence_ids):
            raise ScoringOutputError("评分模型引用了未提供的 evidence chunk_id")
        _validate_applicable_dimensions(case, result)
        return result


def _validate_applicable_dimensions(
    case: EvaluationCase, result: ScoringResult
) -> None:
    """要求范围外用例只评完整性/行为，其他用例四项均给出分数。"""
    scores = result.dimension_scores
    if case.expected_behavior == "answer_and_guide":
        if scores.correctness is not None or scores.grounding is not None:
            raise ScoringOutputError("范围外回答的正确性和证据忠实度应标记为不适用")
        if scores.completeness is None or scores.behavior is None:
            raise ScoringOutputError("范围外回答必须评分完整性和行为符合度")
        return
    if any(
        score is None
        for score in (
            scores.correctness,
            scores.grounding,
            scores.completeness,
            scores.behavior,
        )
    ):
        raise ScoringOutputError("该用例的四个评分维度均适用，不能返回 null")
