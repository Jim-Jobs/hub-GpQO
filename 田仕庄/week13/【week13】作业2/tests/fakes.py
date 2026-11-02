"""测试共用的 LLM 替身（Fake）。

契约与真实 LLM 客户端一致：invoke(messages) -> obj.content；
FakeStreamLLM 额外提供 stream()（逐 token yield），
用于验证 generate_answer 的流式链路与降级回退。
"""

from __future__ import annotations


class FakeResp:
    """最小响应对象：只需 .content 属性。"""

    def __init__(self, content: str):
        self.content = content


class FakeLLM:
    """脚本化 LLM：按顺序弹出预设响应，并记录每次调用。"""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return FakeResp(self.responses.pop(0))


class FakeStreamLLM(FakeLLM):
    """带 stream() 的 Fake LLM，用于验证流式回答链路。"""

    def stream(self, messages, **kwargs):
        self.calls.append(messages)
        for ch in self.responses.pop(0):
            yield FakeResp(ch)
