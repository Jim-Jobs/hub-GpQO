import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.domain.errors import AppError

logger = logging.getLogger("app.api")


def _validation_response(exc: RequestValidationError) -> JSONResponse:
    code = "VALIDATION_ERROR"
    message = "请求参数不合法。"
    for error in exc.errors():
        error_type = str(error.get("type", ""))
        location = error.get("loc", ())
        if error_type == "empty_input":
            code = "EMPTY_INPUT"
            message = "输入不能为空。"
            break
        if "conversation_id" in location and error_type.startswith("uuid"):
            code = "INVALID_CONVERSATION_ID"
            message = "conversation_id 必须是 UUID。"
            break
    return JSONResponse(
        status_code=422, content={"error": {"code": code, "message": message}}
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _validation_response(exc)

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error")
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "服务内部错误。"}},
        )


if __name__ == "__main__":
    print("handlers: AppError, RequestValidationError, HTTPException, Exception")
