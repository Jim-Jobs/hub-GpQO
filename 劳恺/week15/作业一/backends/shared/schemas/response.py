"""情绪响应结果模型。"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from backends.shared.constants.strategies import ResponseStrategy


class ResponseResult(BaseModel):
    """Agent 2 的输出。

    ``is_consistent`` 是 Agent 自评的"回复与情绪是否匹配"，
    FastAPI 编排器还会独立做一次权威质量检查，二者都通过才算通过。
    """

    strategy: ResponseStrategy
    reply: str
    is_consistent: bool = True

    @field_validator("reply", mode="before")
    @classmethod
    def _normalize_reply(cls, value: object) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()
