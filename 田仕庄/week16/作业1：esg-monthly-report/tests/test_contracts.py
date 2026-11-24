"""契约：中间产物 pydantic 模型与导出的 JSON Schema 一致（README §12.1）。"""

from __future__ import annotations

import json

import pytest

contracts = pytest.importorskip("scripts.contracts")


def test_raw_schema_covers_contract_fields():
    schema = contracts.json_schema(contracts.RawArtifact)
    properties = schema["properties"]

    assert {"schema_version", "generated_at", "api", "window", "gaps", "results"} <= set(properties)
    assert properties["window"]["$ref"].endswith("/Window")
    result_props = schema["$defs"]["RawResult"]["properties"]
    assert {"title", "url", "snippet", "published_at", "source"} <= set(result_props)
    assert schema["additionalProperties"] is False


def test_impact_level_enum_in_summarized_schema():
    schema = contracts.json_schema(contracts.SummarizedArtifact)
    item = schema["$defs"]["SummarizedItem"]

    assert item["properties"]["impact_level"]["enum"] == list(contracts.IMPACT_LEVELS)


def test_committed_schemas_match_models():
    for name, model in contracts.ARTIFACTS.items():
        path = contracts.SCHEMA_DIR / f"{name}.schema.json"
        assert path.exists(), f"缺少 {path}，运行 python scripts/contracts.py 重新导出"
        assert json.loads(path.read_text(encoding="utf-8")) == contracts.json_schema(model)


def test_artifact_serialization_roundtrip():
    artifact = contracts.RawArtifact(
        schema_version=contracts.SCHEMA_VERSION,
        generated_at="2026-11-23T10:00:00",
        api="tavily",
        window=contracts.Window(start="2026-09-01", end="2026-10-31"),
        gaps=[],
        results=[
            contracts.RawResult.from_mapping({"title": "演示条目", "url": "https://example.com/a"})
        ],
    )
    payload = contracts.as_dict(artifact)

    assert payload["schema_version"] == "1.0"
    assert payload["window"] == {"start": "2026-09-01", "end": "2026-10-31"}
    assert payload["results"][0]["title"] == "演示条目"
    assert payload["results"][0]["source"] == ""


def test_ranked_result_mapping_parses_score():
    data = {
        "title": "演示条目",
        "url": "https://example.com/a",
        "score": {"relevance": 1.0, "authority": 1.0, "timeliness": 0.5, "total": 0.9},
        "dedup_key": "https://example.com/a",
    }
    result = contracts.RankedResult.from_mapping(data)

    assert result.score.total == 0.9
    assert result.dedup_key == "https://example.com/a"
