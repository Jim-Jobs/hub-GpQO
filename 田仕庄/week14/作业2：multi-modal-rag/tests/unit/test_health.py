from __future__ import annotations


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert body["mock"] is True
    assert body["parse_backend"] == "mock"
    assert body["version"]
    assert response.headers.get("X-Trace-Id")


def test_health_deps_in_mock_mode(client):
    response = client.get("/health/deps")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert set(body["deps"]) == {"kafka", "milvus", "mineru"}
    assert all(item["ok"] for item in body["deps"].values())


def test_trace_header_is_propagated(client):
    response = client.get("/health", headers={"X-Trace-Id": "trace-test-1"})
    assert response.headers["X-Trace-Id"] == "trace-test-1"
