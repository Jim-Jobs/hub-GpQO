"""健康探测单测。"""

from __future__ import annotations

from app.services.health import parse_bootstrap_servers


def test_parse_single_bootstrap_server() -> None:
    assert parse_bootstrap_servers("localhost:9092") == [("localhost", 9092)]


def test_parse_multiple_bootstrap_servers() -> None:
    parsed = parse_bootstrap_servers("kafka:9092, kafka2:9093 ,")
    assert parsed == [("kafka", 9092), ("kafka2", 9093)]


def test_parse_default_port() -> None:
    assert parse_bootstrap_servers("kafka") == [("kafka", 9092)]
