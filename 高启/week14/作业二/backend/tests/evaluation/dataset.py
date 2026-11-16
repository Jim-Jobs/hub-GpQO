"""读取并严格校验固定版本的 33 条黄金问答 JSONL。"""

import hashlib
from collections import Counter
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from backend.tests.evaluation.schemas import EvaluationCase


EVALUATION_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "evaluation"
GOLDEN_CASES_PATH = EVALUATION_ROOT / "qa_golden.jsonl"
EXPECTED_CATEGORY_COUNTS = {
    "FQA": 4,
    "DOC-QA": 4,
    "TableQA": 4,
    "KBQA": 4,
    "TaskQA": 4,
    "scope": 10,
    "multimodal_input": 2,
    "multimodal_output": 1,
}


class EvaluationDatasetError(ValueError):
    """黄金集缺行、字段无效、类别分布错误或图片 fixture 越界。"""


def load_cases(
    cases_path: Path = GOLDEN_CASES_PATH,
    *,
    fixture_root: Path = EVALUATION_ROOT,
) -> tuple[list[EvaluationCase], str]:
    """校验每行用例和图片路径，并返回用例列表与原始数据 SHA-256。"""
    try:
        raw_data = cases_path.read_bytes()
        text = raw_data.decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise EvaluationDatasetError(f"无法读取 UTF-8 黄金集：{cases_path}") from error

    cases: list[EvaluationCase] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = EvaluationCase.model_validate_json(line)
        except ValidationError as error:
            raise EvaluationDatasetError(
                f"黄金集第 {line_number} 行字段无效：{error}"
            ) from error
        if case.id in seen_ids:
            raise EvaluationDatasetError(f"黄金集 case_id 重复：{case.id}")
        seen_ids.add(case.id)
        if case.input_image is not None:
            _validate_image_fixture(case.input_image, fixture_root, case.id)
        cases.append(case)

    category_counts = Counter(case.category for case in cases)
    if category_counts != EXPECTED_CATEGORY_COUNTS:
        raise EvaluationDatasetError(
            "黄金集类别数量不符合首轮 33 条基线："
            f"expected={EXPECTED_CATEGORY_COUNTS}, actual={dict(category_counts)}"
        )
    if len(cases) != 33:
        raise EvaluationDatasetError(f"黄金集必须包含 33 条，实际为 {len(cases)} 条")
    return cases, hashlib.sha256(raw_data).hexdigest()


def _validate_image_fixture(image_path: str, fixture_root: Path, case_id: str) -> None:
    """拒绝绝对路径和目录穿越，并确认用户图 fixture 确实存在。"""
    relative_path = PurePosixPath(image_path.replace("\\", "/"))
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise EvaluationDatasetError(f"用例 {case_id} 的 input_image 必须是安全相对路径")
    root = fixture_root.resolve()
    resolved = (root / Path(*relative_path.parts)).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise EvaluationDatasetError(f"用例 {case_id} 的图片 fixture 不存在或越界：{image_path}")
