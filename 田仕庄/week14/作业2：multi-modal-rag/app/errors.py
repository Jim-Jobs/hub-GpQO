"""稳定错误码与业务异常。新增错误必须同步 ERROR_TEXT。"""

from __future__ import annotations

from typing import Final

UNSUPPORTED_TYPE: Final = "UNSUPPORTED_TYPE"
INVALID_PDF: Final = "INVALID_PDF"
EMPTY_FILE: Final = "EMPTY_FILE"
FILE_TOO_LARGE: Final = "FILE_TOO_LARGE"
TOO_MANY_FILES: Final = "TOO_MANY_FILES"
DUPLICATE: Final = "DUPLICATE"
UPLOAD_FAILED: Final = "UPLOAD_FAILED"

ENQUEUE_FAILED: Final = "ENQUEUE_FAILED"
ENQUEUE_TIMEOUT: Final = "ENQUEUE_TIMEOUT"

MINERU_NOT_INSTALLED: Final = "MINERU_NOT_INSTALLED"
MINERU_TIMEOUT: Final = "MINERU_TIMEOUT"
MINERU_FAILED: Final = "MINERU_FAILED"
PARSE_OUTPUT_MISSING: Final = "PARSE_OUTPUT_MISSING"
PARSE_FAILED: Final = "PARSE_FAILED"
INDEX_FAILED: Final = "INDEX_FAILED"

STALE_PENDING: Final = "STALE_PENDING"
PARSE_INTERRUPTED: Final = "PARSE_INTERRUPTED"

FILE_NOT_FOUND: Final = "FILE_NOT_FOUND"
INVALID_STATE: Final = "INVALID_STATE"
LLM_NOT_CONFIGURED: Final = "LLM_NOT_CONFIGURED"
LLM_FAILED: Final = "LLM_FAILED"
RETRIEVAL_FAILED: Final = "RETRIEVAL_FAILED"
IMAGE_INVALID: Final = "IMAGE_INVALID"
IMAGE_TOO_LARGE: Final = "IMAGE_TOO_LARGE"
TOO_MANY_IMAGES: Final = "TOO_MANY_IMAGES"
QUESTION_EMPTY: Final = "QUESTION_EMPTY"

ERROR_TEXT: Final[dict[str, str]] = {
    UNSUPPORTED_TYPE: "仅支持 PDF 文件",
    INVALID_PDF: "文件不是有效的 PDF",
    EMPTY_FILE: "文件为空",
    FILE_TOO_LARGE: "文件超过大小上限",
    TOO_MANY_FILES: "单次上传文件数量超过上限",
    DUPLICATE: "文件已存在",
    UPLOAD_FAILED: "保存上传文件失败",
    ENQUEUE_FAILED: "解析任务入队失败",
    ENQUEUE_TIMEOUT: "解析任务入队超时",
    MINERU_NOT_INSTALLED: "未找到 MinerU 可执行文件",
    MINERU_TIMEOUT: "MinerU 解析超时",
    MINERU_FAILED: "MinerU 解析失败",
    PARSE_OUTPUT_MISSING: "未找到解析产物",
    PARSE_FAILED: "解析失败",
    INDEX_FAILED: "向量入库失败",
    STALE_PENDING: "排队超时，未被 Worker 消费",
    PARSE_INTERRUPTED: "解析进程中断",
    FILE_NOT_FOUND: "文件不存在",
    INVALID_STATE: "当前状态不允许该操作",
    LLM_NOT_CONFIGURED: "未配置 DASHSCOPE_API_KEY",
    LLM_FAILED: "模型调用失败",
    RETRIEVAL_FAILED: "检索失败",
    IMAGE_INVALID: "图片格式不支持或已损坏",
    IMAGE_TOO_LARGE: "图片超过大小上限",
    TOO_MANY_IMAGES: "图片数量超过上限",
    QUESTION_EMPTY: "请输入问题或上传图片",
}


class AppError(Exception):
    """可映射为 HTTP 的业务异常。"""

    def __init__(
        self,
        code: str,
        message: str | None = None,
        status_code: int = 400,
    ) -> None:
        self.code = code
        self.message = message or ERROR_TEXT.get(code, "请求失败")
        self.status_code = status_code
        super().__init__(self.message)
