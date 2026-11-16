"""数据契约使用的 JSON 序列化辅助函数。"""

from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum
import json
from typing import Any


def to_json(value: Any) -> str:
    """将数据类、枚举和时间统一转换为 UTF-8 友好的 JSON 字符串。"""
    if not is_dataclass(value):
        raise TypeError("to_json 只接受数据类实例")
    return json.dumps(asdict(value), ensure_ascii=False, default=_encode_value)


def _encode_value(value: Any) -> Any:
    """将 JSON 标准库不认识的枚举和时间类型转换为基础值。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(f"不支持 JSON 序列化类型：{type(value).__name__}")


def from_json_values(payload: str) -> dict[str, Any]:
    """解析 JSON 对象并拒绝数组、标量等错误载荷。"""
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("JSON 数据契约必须是对象")
    return value
