"""问答作答层：OpenAI 兼容 VLM（qwen-vl / GPT-4o / 任意兼容网关）+ Mock 兜底。"""

from __future__ import annotations

import base64
import mimetypes
import os
import re
from pathlib import Path
from typing import Iterator

from ..config import settings
from .prompts import SYSTEM_PROMPT, build_user_text

IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")
MAX_IMAGE_BYTES = 12 * 1024 * 1024


def _data_uri(path: str) -> str | None:
    file_path = Path(path)
    if not file_path.is_file():
        return None
    if file_path.stat().st_size > MAX_IMAGE_BYTES:
        return None
    mime, _ = mimetypes.guess_type(file_path.name)
    mime = mime or "image/png"
    payload = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


class BaseAnswerer:
    name: str = "base"

    def stream(
        self,
        question: str,
        context: str,
        image_paths: list[str] | None = None,
        query_image_path: str | None = None,
    ) -> Iterator[str]:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"backend": self.name, "model": None}


class MockAnswerer(BaseAnswerer):
    """未配置 API Key 时的兜底：直接渲染检索结果，保证前后端链路可验证。"""

    name = "mock"

    def stream(
        self,
        question: str,
        context: str,
        image_paths: list[str] | None = None,
        query_image_path: str | None = None,
    ) -> Iterator[str]:
        lines = [
            "> **Mock 模式**：当前未配置 `LLM_API_KEY`，下面直接返回检索到的资料，"
            "用于验证「Kafka → MinerU/PyMuPDF 解析 → 向量化 → 检索 → 图文混排」整条链路。",
            "",
        ]
        if query_image_path:
            lines.append(
                f"已收到用户图片 `{os.path.basename(query_image_path)}`；"
                "配置真实 VLM 后，这张图会和检索到的图文一起送入模型。"
            )
            lines.append("")
        lines.append(f"### 关于「{question.strip() or '（空问题）'}」检索到的资料")
        lines.append("")

        image_lines = IMAGE_MD_RE.findall(context)
        body = IMAGE_MD_RE.sub("", context).strip()
        if body:
            for line in body.split("\n"):
                stripped = line.strip()
                if not stripped:
                    lines.append("")
                elif stripped.startswith("【资料"):
                    lines.append(f"**{stripped}**")
                    lines.append("")
                else:
                    lines.append(stripped)
        else:
            lines.append("（知识库里没有检索到相关片段，请先上传 PDF 并等待解析完成。）")

        if image_lines:
            lines.append("")
            lines.append("### 相关资料图片")
            for url in dict.fromkeys(image_lines):
                if url.startswith("http"):
                    lines.append(f"![资料图片]({url})")
                    lines.append("")

        text = "\n".join(lines)
        # 模拟流式输出，前端可以按增量渲染
        step = 24
        for index in range(0, len(text), step):
            yield text[index : index + step]

    def describe(self) -> dict:
        return {"backend": self.name, "model": "mock"}


class OpenAIAnswerer(BaseAnswerer):
    name = "openai"

    def __init__(self) -> None:
        from openai import OpenAI

        self.client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_sec,
        )
        self.model = settings.llm_model

    def _build_content(
        self,
        question: str,
        context: str,
        image_paths: list[str] | None,
        query_image_path: str | None,
    ) -> list[dict]:
        parts: list[dict] = [
            {"type": "text", "text": build_user_text(question, context)}
        ]
        candidates: list[str] = []
        if query_image_path:
            candidates.append(query_image_path)
        if settings.send_retrieved_images:
            candidates.extend(image_paths or [])

        for path in candidates[: 1 + settings.max_images_to_llm]:
            uri = _data_uri(path)
            if uri:
                parts.append({"type": "image_url", "image_url": {"url": uri}})
        return parts

    def stream(
        self,
        question: str,
        context: str,
        image_paths: list[str] | None = None,
        query_image_path: str | None = None,
    ) -> Iterator[str]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": self._build_content(
                    question, context, image_paths, query_image_path
                ),
            },
        ]
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            stream=True,
        )
        for event in stream:
            choices = getattr(event, "choices", None)
            if not choices:
                continue
            delta = choices[0].delta
            content = getattr(delta, "content", None)
            if content is None:
                continue
            if isinstance(content, str):
                if content:
                    yield content
                continue
            # 部分网关返回分段结构：[{"type": "text", "text": "..."}]
            for part in content:
                text = part.get("text") if isinstance(part, dict) else None
                if text:
                    yield text

    def describe(self) -> dict:
        return {"backend": self.name, "model": self.model}


def get_answerer() -> BaseAnswerer:
    if settings.llm_backend in {"mock", "demo"}:
        return MockAnswerer()
    if not settings.llm_api_key:
        return MockAnswerer()
    return OpenAIAnswerer()
