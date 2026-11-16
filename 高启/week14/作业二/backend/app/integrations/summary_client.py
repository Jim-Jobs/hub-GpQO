"""调用本机 Qwen3-VL 为 MinerU 解析结果生成文档摘要。"""

import json
from typing import Any

from backend.app.documents.domain.models import DocumentSummary, ParsedDocument
from backend.app.integrations.errors import SummaryError


SUMMARY_INPUT_MAX_CHARS = 16_000


class OpenAIDocumentSummaryClient:
    """通过 OpenAI 兼容 chat completions 生成标题和简短中文摘要。"""

    def __init__(self, base_url: str, model: str, *, client: Any | None = None) -> None:
        """保存 Qwen 服务配置；允许注入 SDK 替身覆盖本地和云端调用。"""
        if not base_url:
            raise ValueError("缺少问答模型服务地址")
        if client is None:
            try:
                from openai import OpenAI
            except ImportError as error:
                raise SummaryError("缺少 OpenAI Python SDK，请安装后端依赖") from error
            client = OpenAI(base_url=base_url, api_key="EMPTY")
        self._client = client
        self._model = model

    def summarize(self, parsed: ParsedDocument) -> DocumentSummary:
        """依据 MinerU 标题/目录和正文生成可持久化的 JSON 摘要。"""
        source = _summary_source(parsed)
        prompt = (
            "请根据以下 PDF 解析内容，用中文返回一个 JSON 对象，字段必须是 title 和 summary。"
            "title 用简短标题概括文档主题；summary 用 1 至 3 句话说明文档的主要主题、范围和关键内容。"
            "只能概括输入中明确出现的信息，不要推测，不要输出 Markdown 代码块。\n\n"
            f"文档内容：\n{source}"
        )
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                messages=[
                    {"role": "system", "content": "你负责忠实整理 PDF 文档主题。"},
                    {"role": "user", "content": prompt},
                ],
            )
            content = response.choices[0].message.content
            value = json.loads(content) if isinstance(content, str) else None
        except Exception as error:
            raise SummaryError("Qwen3-VL 文档摘要请求或 JSON 解析失败") from error
        if not isinstance(value, dict):
            raise SummaryError("Qwen3-VL 摘要必须是 JSON 对象")
        title = value.get("title")
        summary = value.get("summary")
        if not isinstance(title, str) or not title.strip():
            raise SummaryError("Qwen3-VL 摘要缺少非空 title")
        if not isinstance(summary, str) or not summary.strip():
            raise SummaryError("Qwen3-VL 摘要缺少非空 summary")
        return DocumentSummary(title=title.strip(), summary=summary.strip())


def _summary_source(parsed: ParsedDocument) -> str:
    """优先保留 Markdown 标题/目录，再按长度上限加入 MinerU 页面正文。"""
    headings = [
        line.strip()
        for line in parsed.markdown.splitlines()
        if line.strip().startswith("#")
    ]
    heading_text = "\n".join(headings)[:4_000]
    remaining = max(0, SUMMARY_INPUT_MAX_CHARS - len(heading_text))
    page_text = "\n\n".join(page.text for page in parsed.pages if page.text.strip())
    if not page_text:
        page_text = "\n".join(
            line for line in parsed.markdown.splitlines() if not line.strip().startswith("#")
        )
    body_text = page_text[:remaining]
    return "\n\n".join(part for part in (heading_text, body_text) if part)
