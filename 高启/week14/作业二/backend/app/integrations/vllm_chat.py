"""使用 OpenAI Python SDK 调用本机 vLLM Qwen3-VL 服务。"""

import base64
from typing import Any

from backend.app.chat.domain.models import ChatImage
from backend.app.integrations.errors import ChatModelError


class OpenAICompatibleChatClient:
    """封装 JSON 文本和多图输入，供问答 Agent 节点共用。"""

    def __init__(self, base_url: str, model: str, *, client: Any | None = None) -> None:
        """保存 vLLM 地址和模型名；SDK client 可注入供单元测试使用。"""
        if not base_url:
            raise ValueError("缺少问答模型服务地址")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise ChatModelError("缺少 OpenAI Python SDK，请安装后端依赖") from error
            client = OpenAI(base_url=base_url, api_key="EMPTY")
        self._client = client
        self._model = model

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[ChatImage] | None = None,
    ) -> str:
        """发送单次 chat completion，将图片转成 OpenAI 兼容 data URL。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
        for image in images or []:
            content.append(_image_content(image))
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": content},
                ],
            )
            result = response.choices[0].message.content
        except Exception as error:
            raise ChatModelError("vLLM Qwen3-VL 请求失败") from error
        if not isinstance(result, str) or not result.strip():
            raise ChatModelError("Qwen3-VL 返回了空文本响应")
        return result.strip()


def _image_content(image: ChatImage) -> dict[str, Any]:
    """检查图像载荷类型，并生成 SDK 可发送的 base64 data URL。"""
    if not image.data or not image.media_type.startswith("image/"):
        raise ChatModelError("问答图像为空或 MIME 类型无效")
    encoded = base64.b64encode(image.data).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
    }
