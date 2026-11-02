"""模型输出解析：JSON 提取 -> 类别归一化 -> 关键词兜底。"""
import json
import re
from dataclasses import dataclass, field

from .prompt import (
    CATEGORY_HIGH,
    CATEGORY_LOW,
    CATEGORY_MEDIUM,
    CATEGORY_NONE,
    CATEGORY_NOT_HALLWAY,
    VALID_CATEGORIES,
)

# 类别 -> 危险等级（供前端着色/排序）
CATEGORY_LEVEL = {
    CATEGORY_NOT_HALLWAY: 0,
    CATEGORY_NONE: 1,
    CATEGORY_LOW: 2,
    CATEGORY_MEDIUM: 3,
    CATEGORY_HIGH: 4,
}

# 常见别名归一化（模型偶发输出近义词）
_CATEGORY_ALIASES = {
    "无隐患": CATEGORY_NONE,
    "没有风险": CATEGORY_NONE,
    "安全": CATEGORY_NONE,
    "中等风险": CATEGORY_MEDIUM,
    "中度风险": CATEGORY_MEDIUM,
    "中危险": CATEGORY_MEDIUM,
    "高危": CATEGORY_HIGH,
    "高危险": CATEGORY_HIGH,
    "不是楼道": CATEGORY_NOT_HALLWAY,
    "非楼梯间": CATEGORY_NOT_HALLWAY,
    "无关图片": CATEGORY_NOT_HALLWAY,
}

# 兜底关键词，注意顺序：先匹配更具体的类别名
_FALLBACK_KEYWORDS = [
    CATEGORY_HIGH,
    CATEGORY_MEDIUM,
    "中等风险",
    CATEGORY_LOW,
    CATEGORY_NONE,
    "无隐患",
    CATEGORY_NOT_HALLWAY,
]


class ClassificationError(Exception):
    """模型输出无法解析为合法分类时抛出。"""


@dataclass
class ClassificationResult:
    category: str
    level: int
    reason: str = ""
    evidence: list = field(default_factory=list)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    return fence.group(1) if fence else text


def _normalize_category(raw: str) -> str | None:
    raw = (raw or "").strip().strip("。；;，,")
    if raw in VALID_CATEGORIES:
        return raw
    if raw in _CATEGORY_ALIASES:
        return _CATEGORY_ALIASES[raw]
    # 包含式匹配：如 "高风险（有电动车）"
    for name in sorted(VALID_CATEGORIES, key=len, reverse=True):
        if name in raw:
            return name
    for alias, name in _CATEGORY_ALIASES.items():
        if alias in raw:
            return name
    return None


def parse_model_output(text: str) -> ClassificationResult:
    """把模型原始输出解析为 ClassificationResult；失败抛 ClassificationError。"""
    if not text or not text.strip():
        raise ClassificationError("模型返回为空")

    cleaned = _strip_code_fence(text)

    # 1) 尝试 JSON 解析（整段或截取第一个 {...}）
    payload = None
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                payload = None

    # 2) JSON 路径
    if isinstance(payload, dict):
        category = _normalize_category(str(payload.get("category", "")))
        if category:
            reason = str(payload.get("reason", "")).strip()
            evidence = payload.get("evidence") or []
            if not isinstance(evidence, list):
                evidence = [str(evidence)]
            evidence = [str(e) for e in evidence][:8]
            return ClassificationResult(
                category=category,
                level=CATEGORY_LEVEL[category],
                reason=reason,
                evidence=evidence,
            )

    # 3) 关键词兜底：自由文本中查找类别词
    for keyword in _FALLBACK_KEYWORDS:
        if keyword in cleaned:
            category = _normalize_category(keyword)
            reason = cleaned.strip().replace("\n", " ")[:200]
            return ClassificationResult(
                category=category,
                level=CATEGORY_LEVEL[category],
                reason=f"[关键词兜底] {reason}",
                evidence=[],
            )

    raise ClassificationError(f"无法解析模型输出: {cleaned[:200]}")
