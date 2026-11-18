import os

# httpx 0.28.1 无法解析 NO_PROXY 中的 IPv6 方括号写法，会导致 TestClient 构造失败。
for _proxy_key in ("NO_PROXY", "no_proxy"):
    _proxy_value = os.environ.get(_proxy_key)
    if _proxy_value:
        os.environ[_proxy_key] = ",".join(
            part for part in _proxy_value.split(",") if part.strip() != "[::1]"
        )

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

ENGINES = ["langgraph", "openai-agents"]


class FakeLLMState(BaseModel):
    """按顺序返回预置输出；队列耗尽后返回默认回复。"""

    outputs: list[str] = Field(default_factory=list)
    default_reply: str = "别灰心，先允许自己难过一会儿，再想想下一步。"
    mode: str = "ok"  # ok | timeout | rate_limit
    requests: list[str] = Field(default_factory=list)


def _completion(content: str) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "qwen-flash",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


@pytest.fixture(scope="session")
def database_url():
    url = os.getenv("TEST_DATABASE_URL")
    if url:
        yield url
        return

    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16") as container:
        raw = container.get_connection_url()
        yield raw.replace("postgresql+psycopg2", "postgresql+asyncpg").replace(
            "postgresql://", "postgresql+asyncpg://"
        )


@pytest.fixture(scope="session")
def migrated_db(database_url):
    os.environ["DATABASE_URL"] = database_url

    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")
    return database_url


@pytest.fixture(params=ENGINES, ids=ENGINES)
def engine(request):
    return request.param


@pytest.fixture()
def fake_llm(monkeypatch):
    import httpx2
    from langchain_openai.chat_models import _client_utils

    # langchain-openai 缓存默认 HTTP 客户端；不清空会把上一个测试的 mock transport 带进下一个测试。
    _client_utils._cached_async_httpx_client.cache_clear()
    _client_utils._cached_sync_httpx_client.cache_clear()

    state = FakeLLMState()

    def handler(request: httpx2.Request) -> httpx2.Response:
        state.requests.append(request.content.decode("utf-8"))
        if state.mode == "timeout":
            raise httpx2.TimeoutException("fake timeout", request=request)
        if state.mode == "rate_limit":
            return httpx2.Response(
                429,
                json={
                    "error": {
                        "message": "rate limited",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                },
            )
        content = state.outputs.pop(0) if state.outputs else state.default_reply
        return httpx2.Response(200, json=_completion(content))

    transport = httpx2.MockTransport(handler)
    original_init = httpx2.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx2.AsyncClient, "__init__", patched_init)
    yield state


@pytest.fixture()
def client(engine, migrated_db, monkeypatch, fake_llm):
    monkeypatch.setenv("DATABASE_URL", migrated_db)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("PIPELINE_ENGINE", engine)
    monkeypatch.delenv("API_TOKEN", raising=False)

    from app.config import Settings
    from app.main import create_app

    with TestClient(create_app(Settings(_env_file=None))) as test_client:
        yield test_client


if __name__ == "__main__":
    from pathlib import Path

    raise SystemExit(pytest.main([str(Path(__file__).parent), "-q"]))
