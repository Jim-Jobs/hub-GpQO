import pytest
from pydantic import ValidationError

from app.config import Settings


def _settings(**overrides) -> Settings:
    values = {"api_token": None, **overrides}
    return Settings(_env_file=None, dashscope_api_key="test-key", **values)


def test_defaults():
    settings = _settings()
    assert settings.pipeline_engine == "langgraph"
    assert settings.qwen_model == "qwen-flash"
    assert settings.llm_timeout_seconds == 30
    assert settings.max_input_length == 4000
    assert settings.api_token is None


def test_api_token_is_optional_and_trimmed():
    assert _settings(api_token="   ").api_token is None

    token = _settings(api_token="  " + "a" * 16 + "  ").api_token
    assert token is not None
    assert token.get_secret_value() == "a" * 16


def test_rejects_short_api_token():
    with pytest.raises(ValidationError):
        _settings(api_token="too-short")


@pytest.mark.parametrize("engine", ["langgraph", "openai-agents"])
def test_accepts_supported_engines(engine):
    assert _settings(pipeline_engine=engine).pipeline_engine == engine


@pytest.mark.parametrize("engine", ["", "langchain", "openai"])
def test_rejects_unknown_engine(engine):
    with pytest.raises(ValidationError):
        _settings(pipeline_engine=engine)


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
