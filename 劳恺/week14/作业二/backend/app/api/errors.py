"""统一错误响应。

对应文档：``docs/04-API契约.md`` §1：

```json
{"detail": "...", "code": "..."}
```

FastAPI 默认把 ``HTTPException.detail`` 包成 ``{"detail": ...}``，拿不到 ``code``，
所以自定义一个异常 + 处理器。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    """业务错误。``detail`` 必须是用户可读的中文。"""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def not_found(detail: str = "资源不存在") -> ApiError:
    return ApiError(404, "not_found", detail)


def conflict(detail: str) -> ApiError:
    return ApiError(409, "conflict", detail)


def bad_request(detail: str) -> ApiError:
    return ApiError(400, "invalid_request", detail)


def payload_too_large(detail: str) -> ApiError:
    return ApiError(413, "payload_too_large", detail)


def service_unavailable(detail: str) -> ApiError:
    return ApiError(503, "service_unavailable", detail)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _handle_api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )
