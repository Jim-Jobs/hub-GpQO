"""健康检查：基础状态与依赖连通性。"""

from __future__ import annotations

import shutil
import socket
from urllib.parse import urlparse

from fastapi import APIRouter
from sqlalchemy import text

from app import __version__
from app.config import get_settings
from app.db import engine
from app.schemas import DependencyStatus, DepsResponse, HealthResponse

router = APIRouter(tags=["health"])


def _db_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # pragma: no cover - 仅在数据库损坏时触发
        return False


def _tcp_ok(host: str, port: int, timeout: float = 0.8) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    db_ok = _db_ok()
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        mock=settings.mock_external,
        parse_backend=settings.parse_backend,
        db="ok" if db_ok else "error",
        log_dir=str(settings.logs_dir),
    )


@router.get("/health/deps", response_model=DepsResponse)
def health_deps() -> DepsResponse:
    settings = get_settings()
    deps: dict[str, DependencyStatus] = {}

    if settings.mock_external:
        deps["kafka"] = DependencyStatus(ok=True, detail="mock")
        deps["milvus"] = DependencyStatus(ok=True, detail="mock")
        deps["mineru"] = DependencyStatus(ok=True, detail="mock")
    else:
        host, _, port = settings.kafka_bootstrap.partition(":")
        deps["kafka"] = DependencyStatus(
            ok=_tcp_ok(host or "localhost", int(port or 9092)),
            detail=settings.kafka_bootstrap,
        )

        uri = settings.milvus_uri
        if uri and not uri.startswith(("http://", "https://")):
            deps["milvus"] = DependencyStatus(
                ok=True, detail=f"milvus-lite: {uri}"
            )
        else:
            parsed = urlparse(uri)
            deps["milvus"] = DependencyStatus(
                ok=_tcp_ok(parsed.hostname or "localhost", parsed.port or 19530),
                detail=uri or "unset",
            )

        mineru_path = settings.mineru_cli_path or shutil.which("mineru")
        if settings.mineru_cli_mode.lower() == "v4":
            try:
                import httpx

                url = settings.mineru_server_url.rstrip("/") + "/api/v1/server/status"
                probe = httpx.get(url, timeout=1.5)
                deps["mineru"] = DependencyStatus(
                    ok=bool(mineru_path) and probe.status_code < 500,
                    detail=(
                        f"cli={mineru_path or 'missing'} "
                        f"server={url} -> {probe.status_code}"
                    ),
                )
            except Exception as exc:
                deps["mineru"] = DependencyStatus(
                    ok=False,
                    detail=f"{settings.mineru_server_url} 不可达: {exc}",
                )
        elif settings.mineru_backend.startswith("vlm"):
            try:
                import httpx

                url = settings.mineru_backend_url.rstrip("/") + "/health"
                probe = httpx.get(url, timeout=1.0)
                deps["mineru"] = DependencyStatus(
                    ok=probe.status_code < 500,
                    detail=f"{url} -> {probe.status_code}",
                )
            except Exception as exc:
                deps["mineru"] = DependencyStatus(
                    ok=False,
                    detail=f"{settings.mineru_backend_url} 不可达: {exc}",
                )
        else:
            deps["mineru"] = DependencyStatus(
                ok=mineru_path is not None,
                detail=mineru_path or "mineru not in PATH",
            )

    status = "ok" if all(item.ok for item in deps.values()) else "degraded"
    return DepsResponse(status=status, deps=deps)
