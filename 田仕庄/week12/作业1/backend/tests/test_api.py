"""API 契约测试：mock 模型服务，验证接口行为与异常分支。"""
import base64

import pytest
from fastapi.testclient import TestClient

from app import main
from app.classifier import ClassificationError, ClassificationResult
from app.config import settings
from app.services.vision import VisionServiceError

# 1x1 PNG
TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


class StubVisionService:
    def __init__(self, result=None, exc: Exception | None = None):
        self._result = result
        self._exc = exc

    def recognize(self, image_base64: str, mime_type: str):
        if self._exc:
            raise self._exc
        return self._result, 123


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def restore_vision_service():
    original = main.vision_service
    yield
    main.vision_service = original


def _post(client, payload):
    return client.post("/api/v1/recognize", json=payload)


class TestHealth:
    def test_health(self, client):
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "model" in body


class TestRecognizeSuccess:
    def test_success(self, client):
        main.vision_service = StubVisionService(
            result=ClassificationResult(category="高风险", level=4, reason="电动车", evidence=["电动车"])
        )
        resp = _post(client, {"image_base64": TINY_PNG_B64, "mime_type": "image/png"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["category"] == "高风险"
        assert body["level"] == 4
        assert body["latency_ms"] == 123
        assert body["request_id"]

    def test_data_url_prefix_tolerated(self, client):
        main.vision_service = StubVisionService(
            result=ClassificationResult(category="无风险", level=1)
        )
        resp = _post(client, {
            "image_base64": f"data:image/png;base64,{TINY_PNG_B64}",
            "mime_type": "image/png",
        })
        assert resp.status_code == 200


class TestRecognizeValidation:
    def test_invalid_base64(self, client):
        resp = _post(client, {"image_base64": "!!!not-base64!!!", "mime_type": "image/png"})
        assert resp.status_code == 422

    def test_unsupported_mime(self, client):
        resp = _post(client, {"image_base64": TINY_PNG_B64, "mime_type": "image/tiff"})
        assert resp.status_code == 422

    def test_oversized_image(self, client, monkeypatch):
        monkeypatch.setattr(settings, "max_image_mb", 0)
        resp = _post(client, {"image_base64": TINY_PNG_B64, "mime_type": "image/png"})
        assert resp.status_code == 413

    def test_missing_field(self, client):
        resp = _post(client, {"mime_type": "image/png"})
        assert resp.status_code == 422


class TestRecognizeFailures:
    def test_service_unavailable(self, client):
        main.vision_service = None
        resp = _post(client, {"image_base64": TINY_PNG_B64, "mime_type": "image/png"})
        assert resp.status_code == 503

    def test_model_call_failure(self, client):
        main.vision_service = StubVisionService(exc=VisionServiceError("连接超时"))
        resp = _post(client, {"image_base64": TINY_PNG_B64, "mime_type": "image/png"})
        assert resp.status_code == 502
        assert resp.json()["success"] is False

    def test_parse_failure(self, client):
        main.vision_service = StubVisionService(exc=ClassificationError("无法解析"))
        resp = _post(client, {"image_base64": TINY_PNG_B64, "mime_type": "image/png"})
        assert resp.status_code == 502


class TestFrontendHosting:
    def test_index_served(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
