"""FastAPI 入口：识别接口 + 健康检查 + 前端页面托管。"""
import base64
import binascii
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from .classifier import ClassificationError
from .config import settings
from .logger import get_audit_logger, setup_logging
from .schemas import ErrorResponse, RecognizeRequest, RecognizeResponse
from .services.vision import VisionService, VisionServiceError

vision_service: VisionService | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global vision_service
    setup_logging()
    logger.info("服务启动中... model={} base_url={}", settings.model_name, settings.deepseek_base_url)
    try:
        vision_service = VisionService()
        logger.info("VisionService 初始化成功")
    except VisionServiceError as exc:
        vision_service = None
        logger.error("VisionService 初始化失败: {}", exc)
    yield
    logger.info("服务已停止")


app = FastAPI(title="楼道消防隐患识别", version="1.0.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: {} {}", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"success": False, "request_id": "", "error": "服务器内部错误"},
    )


@app.get("/api/v1/health")
async def health():
    return {
        "status": "ok",
        "model": settings.model_name,
        "vision_ready": vision_service is not None,
    }


@app.post(
    "/api/v1/recognize",
    response_model=RecognizeResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
               502: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
async def recognize(req: RecognizeRequest):
    request_id = uuid.uuid4().hex[:8]
    audit = get_audit_logger().bind(request_id=request_id)

    # 图片大小校验（base64 解码后的真实字节数）
    try:
        image_bytes = base64.b64decode(req.image_base64, validate=True)
    except (binascii.Error, ValueError):
        logger.warning("[{}] base64 解码失败", request_id)
        return JSONResponse(
            status_code=400,
            content={"success": False, "request_id": request_id, "error": "图片 base64 非法"},
        )
    if len(image_bytes) > settings.max_image_bytes:
        logger.warning("[{}] 图片超限: {} bytes", request_id, len(image_bytes))
        return JSONResponse(
            status_code=413,
            content={
                "success": False,
                "request_id": request_id,
                "error": f"图片超过大小限制 {settings.max_image_mb}MB",
            },
        )

    if vision_service is None:
        logger.error("[{}] 识别服务不可用（API Key 未配置）", request_id)
        return JSONResponse(
            status_code=503,
            content={"success": False, "request_id": request_id, "error": "识别服务未配置"},
        )

    logger.info("[{}] 收到识别请求: mime={} size={}KB", request_id, req.mime_type, len(image_bytes) // 1024)

    try:
        result, latency_ms = vision_service.recognize(req.image_base64, req.mime_type)
    except VisionServiceError as exc:
        logger.error("[{}] 模型调用失败: {}", request_id, exc)
        audit.error("识别失败 request_id={} 原因=模型调用失败: {}", request_id, exc)
        return JSONResponse(
            status_code=502,
            content={"success": False, "request_id": request_id, "error": f"模型调用失败: {exc}"},
        )
    except ClassificationError as exc:
        logger.error("[{}] 结果解析失败: {}", request_id, exc)
        audit.error("识别失败 request_id={} 原因=模型输出解析失败", request_id)
        return JSONResponse(
            status_code=502,
            content={"success": False, "request_id": request_id, "error": "模型输出解析失败"},
        )

    # 审计日志：每次识别的输入摘要 + 结果 + 耗时
    audit.info(
        "识别完成 request_id={} category={} level={} latency_ms={} size_kb={} reason={} evidence={}",
        request_id, result.category, result.level, latency_ms,
        len(image_bytes) // 1024, result.reason, result.evidence,
    )
    logger.info("[{}] 识别结果: {} (level={}, {}ms)", request_id, result.category, result.level, latency_ms)

    return RecognizeResponse(
        success=True,
        request_id=request_id,
        category=result.category,
        level=result.level,
        reason=result.reason,
        evidence=result.evidence,
        latency_ms=latency_ms,
    )


# 前端静态页面（最后挂载，避免覆盖 API 路由）
if settings.frontend_dir.exists():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
else:
    @app.get("/")
    async def index():
        return FileResponse(str(settings.frontend_dir / "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
