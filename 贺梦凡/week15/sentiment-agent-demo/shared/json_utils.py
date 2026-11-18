"""从模型文本中解析 JSON（兼容未严格 structured-output 的网关）。"""

from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def parse_model_json(text: str, model: type[T]) -> T:
    """尝试从纯 JSON 或 markdown 代码块中解析为 Pydantic 模型。"""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    else:
        match = re.search(r"\{[\s\S]*\}", raw)
        if match:
            raw = match.group(0)
    return model.model_validate(json.loads(raw))
