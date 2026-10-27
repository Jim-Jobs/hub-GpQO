"""请求/响应数据模型。"""
import base64
import binascii

from pydantic import BaseModel, Field, field_validator

ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class RecognizeRequest(BaseModel):
    image_base64: str = Field(..., min_length=16, description="图片 base64（不含 data URL 前缀）")
    mime_type: str = Field(default="image/jpeg", description="图片 MIME 类型")

    @field_validator("mime_type")
    @classmethod
    def _check_mime(cls, v: str) -> str:
        v = (v or "").lower().strip()
        if v not in ALLOWED_MIME_TYPES:
            raise ValueError(f"不支持的图片类型: {v}，仅支持 {sorted(ALLOWED_MIME_TYPES)}")
        return v

    @field_validator("image_base64")
    @classmethod
    def _check_base64(cls, v: str) -> str:
        v = v.strip()
        # 容忍误传 data URL 前缀
        if v.startswith("data:"):
            v = v.split(",", 1)[-1]
        try:
            base64.b64decode(v, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("image_base64 不是合法的 base64 编码") from exc
        return v


class RecognizeResponse(BaseModel):
    success: bool
    request_id: str
    category: str
    level: int
    reason: str
    evidence: list[str]
    latency_ms: int


class ErrorResponse(BaseModel):
    success: bool = False
    request_id: str
    error: str
