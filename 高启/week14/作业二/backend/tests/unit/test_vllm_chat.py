"""覆盖 vLLM OpenAI 兼容问答客户端的文本和图像载荷转换。"""

import base64
from types import SimpleNamespace

import pytest

from backend.app.chat.domain.models import ChatImage
from backend.app.integrations.errors import ChatModelError
from backend.app.integrations.vllm_chat import OpenAICompatibleChatClient


def test_chat_client_sends_images_as_openai_data_urls() -> None:
    """多个 PDF/用户图均以其 MIME 类型和原始字节编码后发送。"""

    class FakeCompletions:
        """捕获 SDK 请求并返回符合 Chat Completions 形状的结果。"""

        def __init__(self) -> None:
            """初始化请求捕获槽。"""
            self.request = None

        def create(self, **request) -> SimpleNamespace:
            """保存请求参数并返回固定模型文本。"""
            self.request = request
            message = SimpleNamespace(content='{"ok": true}')
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    completions = FakeCompletions()
    client = OpenAICompatibleChatClient(
        "http://localhost:8000/v1",
        "Qwen/Qwen3-VL",
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        ),
    )

    response = client.complete(
        "系统约束",
        "问题和 PDF 证据",
        [ChatImage(b"user", "image/jpeg"), ChatImage(b"pdf", "image/png")],
    )

    assert response == '{"ok": true}'
    content = completions.request["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "问题和 PDF 证据"}
    assert content[1]["image_url"]["url"] == (
        "data:image/jpeg;base64," + base64.b64encode(b"user").decode("ascii")
    )
    assert content[2]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(b"pdf").decode("ascii")
    )


def test_chat_client_rejects_non_image_payload_before_request() -> None:
    """不符合 image MIME 的图像载荷在请求 vLLM 前被拒绝。"""

    class UnexpectedClient:
        """在测试不应触发模型请求时抛出断言错误。"""

        def __getattr__(self, name: str) -> object:
            """阻止任何 OpenAI SDK 属性被访问。"""
            raise AssertionError("无效图片不应发送到模型服务")

    client = OpenAICompatibleChatClient(
        "http://localhost:8000/v1", "Qwen/Qwen3-VL", client=UnexpectedClient()
    )

    with pytest.raises(ChatModelError, match="MIME 类型无效"):
        client.complete("系统约束", "问题", [ChatImage(b"bytes", "application/pdf")])
