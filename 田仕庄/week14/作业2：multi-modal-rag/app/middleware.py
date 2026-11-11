"""HTTP 横切：trace_id 注入、请求耗时与访问日志。"""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.logging_setup import trace_id_var

logger = logging.getLogger("rag.http")


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex[:16]
        token = trace_id_var.set(trace_id)
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            logger.exception(
                "unhandled error: %s %s", request.method, request.url.path
            )
            raise
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            level = (
                logging.DEBUG
                if request.url.path.startswith("/health")
                else logging.INFO
            )
            logger.log(
                level,
                "%s %s -> %s %.1fms",
                request.method,
                request.url.path,
                status,
                duration_ms,
            )
            trace_id_var.reset(token)

        response.headers["X-Trace-Id"] = trace_id
        return response
