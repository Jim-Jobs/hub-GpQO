import pytest


def test_health_reports_status_db_and_engine(client, engine):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "engine": engine}


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
