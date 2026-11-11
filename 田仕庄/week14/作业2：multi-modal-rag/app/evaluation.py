"""评测口径：文件名 0.25 + 页码 0.25 + 答案 Jaccard 0.5。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_NORMALIZE = re.compile(r"[^\w\u4e00-\u9fff]+")


@dataclass
class EvalCase:
    question: str
    expected_answer: str = ""
    expected_filename: str | None = None
    expected_page: str | None = None


def load_cases(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            cases.append(
                EvalCase(
                    question=item["question"],
                    expected_answer=item.get("expected_answer", ""),
                    expected_filename=item.get("expected_filename"),
                    expected_page=item.get("expected_page"),
                )
            )
    return cases


def _normalize(text: str) -> str:
    return _NORMALIZE.sub("", (text or "").lower())


def _bigrams(text: str) -> set[str]:
    normalized = _normalize(text)
    if not normalized:
        return set()
    if len(normalized) == 1:
        return {normalized}
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def jaccard(left: str, right: str) -> float:
    left_grams = _bigrams(left)
    right_grams = _bigrams(right)
    if not left_grams and not right_grams:
        return 1.0
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def score_case(case: EvalCase, response: dict) -> dict:
    sources = response.get("sources") or []
    predicted_filename = sources[0].get("file_name") if sources else None
    predicted_page = sources[0].get("page") if sources else None

    if case.expected_filename:
        filename_score = float(
            any(
                case.expected_filename in (source.get("file_name") or "")
                for source in sources
            )
        )
    else:
        filename_score = 1.0

    if case.expected_page:
        page_score = float(
            any(
                str(source.get("page")) == str(case.expected_page)
                for source in sources
            )
        )
    else:
        page_score = 1.0

    content_score = jaccard(
        case.expected_answer, response.get("answer", "")
    )
    total = 0.25 * filename_score + 0.25 * page_score + 0.5 * content_score

    return {
        "question": case.question,
        "filename_score": filename_score,
        "page_score": page_score,
        "content_score": round(content_score, 4),
        "total_score": round(total, 4),
        "predicted_filename": predicted_filename,
        "predicted_page": str(predicted_page) if predicted_page else None,
    }
