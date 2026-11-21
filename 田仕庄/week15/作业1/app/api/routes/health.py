import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import get_app_settings, get_session_factory
from app.infra.db.session import ping_database

logger = logging.getLogger("app.health")
router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    settings = get_app_settings(request)
    try:
        await ping_database(get_session_factory(request))
    except Exception:
        logger.warning("database_unreachable")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "db": "error", "engine": settings.pipeline_engine},
        )
    return {"status": "ok", "db": "ok", "engine": settings.pipeline_engine}


if __name__ == "__main__":
    print(router.routes[0].path)
