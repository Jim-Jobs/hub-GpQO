"""LLM 配置层测试：思维链开关、快慢双模型（离线，不触网）。"""

import pytest

from sql_agent import config
from sql_agent import llm as llm_mod


# ---------------------------------------------------------------------------
# 思维链参数解析
# ---------------------------------------------------------------------------
def test_thinking_auto_disables_for_reasoning_models(monkeypatch):
    """auto 模式下推理模型自动关闭思维链（核心提速手段）。"""
    monkeypatch.setattr(config, "LLM_THINKING", "auto")
    for model in ("glm-4.5-air", "glm-4.5-flash", "glm-5", "glm-4.6"):
        assert llm_mod.resolve_extra_body(model) == {"thinking": {"type": "disabled"}}


def test_thinking_not_injected_for_non_reasoning_models(monkeypatch):
    """非推理模型 / 其他供应商不注入参数，避免 400 报错。"""
    monkeypatch.setattr(config, "LLM_THINKING", "auto")
    for model in ("glm-4-flash", "gpt-4o-mini", "deepseek-chat"):
        assert llm_mod.resolve_extra_body(model) == {}


def test_thinking_explicit_enabled(monkeypatch):
    """显式开启：复杂推理场景可恢复深度思考。"""
    monkeypatch.setattr(config, "LLM_THINKING", "enabled")
    assert llm_mod.resolve_extra_body("glm-4.5-air") == {"thinking": {"type": "enabled"}}


def test_thinking_disabled_ignores_unsupported_model(monkeypatch):
    """显式关闭但模型不支持 thinking 时，仍不注入（保证跨供应商可移植）。"""
    monkeypatch.setattr(config, "LLM_THINKING", "disabled")
    assert llm_mod.resolve_extra_body("gpt-4o") == {}


def test_is_reasoning_model():
    assert llm_mod.is_reasoning_model("glm-4.5-air")
    assert llm_mod.is_reasoning_model("GLM-5-Preview".lower())
    assert not llm_mod.is_reasoning_model("glm-4-flash")


# ---------------------------------------------------------------------------
# 客户端构建
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_key(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "test-key.test-secret")


def test_get_llm_applies_thinking_and_retries(fake_key, monkeypatch):
    """客户端应带上 thinking 参数与收紧后的重试次数。"""
    monkeypatch.setattr(config, "LLM_MODEL", "glm-4.5-air")
    monkeypatch.setattr(config, "LLM_THINKING", "auto")

    client = llm_mod.get_llm()

    assert client.model_name == "glm-4.5-air"
    assert client.extra_body == {"thinking": {"type": "disabled"}}
    assert client.max_retries == config.LLM_MAX_RETRIES


def test_get_llm_requires_api_key(monkeypatch):
    """未配置 key 时应给出明确错误（而不是发起请求才失败）。"""
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        llm_mod.get_llm()


def test_fast_llm_falls_back_to_main(fake_key, monkeypatch):
    """LLM_FAST_MODEL 留空时，辅助模型与主模型一致。"""
    monkeypatch.setattr(config, "LLM_MODEL", "glm-4.5-air")
    monkeypatch.setattr(config, "LLM_FAST_MODEL", "")

    assert llm_mod.get_fast_llm().model_name == "glm-4.5-air"


def test_fast_llm_uses_configured_model(fake_key, monkeypatch):
    """配置了辅助模型时，辅助节点走更快的模型。"""
    monkeypatch.setattr(config, "LLM_MODEL", "glm-4.5-flash")
    monkeypatch.setattr(config, "LLM_FAST_MODEL", "glm-4.5-air")

    assert llm_mod.get_fast_llm().model_name == "glm-4.5-air"


def test_describe_llm(monkeypatch):
    """启动日志摘要应包含模型与思维链状态。"""
    monkeypatch.setattr(config, "LLM_MODEL", "glm-4.5-air")
    monkeypatch.setattr(config, "LLM_FAST_MODEL", "")
    monkeypatch.setattr(config, "LLM_THINKING", "auto")

    desc = llm_mod.describe_llm()
    assert "glm-4.5-air" in desc
    assert "disabled" in desc


# ---------------------------------------------------------------------------
# 轻量客户端（native 后端）行为
# ---------------------------------------------------------------------------
def _client(extra_body=None):
    from sql_agent.llm_client import ChatClient

    return ChatClient(base_url="https://example.com/api/paas/v4",
                      api_key="k.s", model="glm-4.5-air",
                      extra_body=extra_body)


def test_client_maps_langchain_roles():
    """LangChain 风格角色必须映射成 OpenAI 角色（否则服务端报"角色信息不正确"）。"""
    payload = _client()._payload(
        [("system", "你是助手"), ("human", "问题"), ("ai", "回答")], False
    )
    assert [m["role"] for m in payload["messages"]] == ["system", "user", "assistant"]
    assert payload["stream"] is False


def test_client_injects_extra_body():
    """thinking 等附加参数应平铺进请求体，并透传 max_tokens。"""
    from sql_agent.llm_client import ChatClient

    c = ChatClient(base_url="https://example.com/v4", api_key="k.s",
                   model="glm-4.5-air", max_tokens=256,
                   extra_body={"thinking": {"type": "disabled"}})
    payload = c._payload([("human", "hi")], True)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 256
    assert payload["stream"] is True


def test_client_parse_sse_line():
    """SSE 解析：正常增量、空增量、[DONE] 与非 data 行。"""
    from sql_agent.llm_client import ChatClient

    ok = 'data: {"choices": [{"delta": {"content": "你好"}}]}'
    assert ChatClient.parse_sse_line(ok) == "你好"

    empty = 'data: {"choices": [{"delta": {}}]}'
    assert ChatClient.parse_sse_line(empty) is None

    assert ChatClient.parse_sse_line("data: [DONE]") is None
    assert ChatClient.parse_sse_line(": keep-alive") is None
    assert ChatClient.parse_sse_line('data: {"choices": []}') is None


def test_client_invoke_retries_then_succeeds(monkeypatch):
    """invoke 失败应重试，成功后返回内容（不触网：替换 httpx 调用）。"""
    from sql_agent import llm_client as mod

    c = mod.ChatClient(base_url="https://example.com/v4", api_key="k.s",
                       model="glm-4.5-air", max_retries=1)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    class FakeResp:
        def __init__(self, code, body):
            self.status_code = code
            self._body = body
            self.text = str(body)

        def json(self):
            return self._body

    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return FakeResp(429, "rate limited")
        return FakeResp(200, {"choices": [{"message": {"content": "最终答案"}}]})

    class FakeHTTP:
        def post(self, *a, **kw):
            return fake_post()

    monkeypatch.setattr(type(c), "http", property(lambda self: FakeHTTP()))
    assert c.invoke([("human", "q")]).content == "最终答案"
    assert calls["n"] == 2


def test_client_invoke_raises_after_retries(monkeypatch):
    """重试耗尽后应抛 LLMError（便于上层捕获）。"""
    from sql_agent import llm_client as mod

    c = mod.ChatClient(base_url="https://example.com/v4", api_key="k.s",
                       model="glm-4.5-air", max_retries=0)
    monkeypatch.setattr(mod.time, "sleep", lambda *_: None)

    class FakeResp:
        status_code = 401
        text = '{"error": "bad key"}'

    class FakeHTTP:
        def post(self, *a, **kw):
            return FakeResp()

    monkeypatch.setattr(type(c), "http", property(lambda self: FakeHTTP()))
    with pytest.raises(mod.LLMError):
        c.invoke([("human", "q")])
