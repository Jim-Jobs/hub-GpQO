from __future__ import annotations

from pathlib import Path

from app.evaluation import EvalCase, jaccard, load_cases, score_case

ROOT = Path(__file__).resolve().parents[2]


def test_jaccard_identical_and_disjoint():
    assert jaccard("产品A下降", "产品A下降") == 1.0
    assert jaccard("产品A下降", "完全不同的内容") == 0.0


def test_score_case_full_match():
    case = EvalCase(
        question="问题",
        expected_answer="Q3销售额下降18%",
        expected_filename="年报.pdf",
        expected_page="3",
    )
    response = {
        "answer": "Q3销售额下降18%",
        "sources": [{"file_name": "年报.pdf", "page": "3"}],
    }

    result = score_case(case, response)

    assert result["filename_score"] == 1.0
    assert result["page_score"] == 1.0
    assert result["content_score"] == 1.0
    assert result["total_score"] == 1.0


def test_score_case_without_expectations():
    case = EvalCase(question="问题", expected_answer="答案")
    response = {"answer": "答案", "sources": []}

    result = score_case(case, response)

    assert result["filename_score"] == 1.0
    assert result["page_score"] == 1.0
    assert result["total_score"] == 1.0


def test_load_sample_cases():
    cases = load_cases(ROOT / "tests" / "fixtures" / "sample_eval.jsonl")
    assert len(cases) == 3
    assert cases[0].question
