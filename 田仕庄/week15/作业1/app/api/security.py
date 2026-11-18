import secrets

from fastapi import Depends, Header

from app.api.deps import get_app_settings
from app.config import Settings
from app.domain.errors import unauthorized_error


async def require_api_token(
    x_api_token: str | None = Header(default=None, alias="X-API-Token"),
    settings: Settings = Depends(get_app_settings),
) -> None:
    expected = settings.api_token
    if expected is None:
        return
    provided = (x_api_token or "").encode("utf-8")
    expected_bytes = expected.get_secret_value().encode("utf-8")
    if not secrets.compare_digest(provided, expected_bytes):
        raise unauthorized_error()


if __name__ == "__main__":
    print("header: X-API-Token")