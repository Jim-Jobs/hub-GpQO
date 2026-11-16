"""图片 caption：用 Qwen3-VL 给每张图写 1~2 句描述。

对应文档：``docs/02-数据模型.md`` §2.3、``docs/09-开发计划.md`` M2 任务 2.5。

**为什么需要 caption**：图片本身没有可靠的文本表示。有了 caption 之后，
图片内容才能被 BM25 关键词命中（"营收柱状图" 这类查询能直接召回该图）。

并发度 4 —— MinerU 配额与 vLLM 吞吐的折中；太高会打满 vLLM 的 KV cache。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from app.llm.image_utils import compress_to_data_url
from app.llm.vllm_client import LLMClient

logger = logging.getLogger(__name__)

CAPTION_PROMPT = (
    "请用 1~2 句简体中文描述这张图片的内容，供文档检索使用。\n"
    "要求：\n"
    "1. 如果是图表，说明图表类型、横纵轴含义、以及关键数值或趋势；\n"
    "2. 如果是表格截图，概括表格主题与主要列；\n"
    "3. 如果是示意图/流程图，说明它表达了什么结构或流程；\n"
    "4. 不要编造图中不存在的信息；无法判断时直接说明。\n"
    "只输出描述本身，不要加任何前缀。"
)


@dataclass
class CaptionResult:
    image_id: str
    caption: str
    error: str | None = None


class Captioner:
    """批量给图片生成描述。单张失败不影响其他图片。"""

    def __init__(
        self,
        llm: LLMClient,
        *,
        concurrency: int = 4,
        max_edge: int = 1600,
        quality: int = 85,
    ) -> None:
        self._llm = llm
        self._concurrency = max(1, concurrency)
        self._max_edge = max_edge
        self._quality = quality

    async def caption_one(self, image_id: str, path: Path) -> CaptionResult:
        try:
            data_url = compress_to_data_url(
                path, max_edge=self._max_edge, quality=self._quality
            )
            text = await self._llm.chat(
                [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": CAPTION_PROMPT},
                        ],
                    }
                ],
                max_tokens=200,
                temperature=0.1,
            )
            return CaptionResult(image_id=image_id, caption=text.strip())
        except Exception as exc:  # noqa: BLE001 —— 单张失败必须降级而不是中断整份文档
            logger.warning("图片 caption 生成失败: %s", image_id, exc_info=True)
            return CaptionResult(image_id=image_id, caption="", error=str(exc))

    async def caption_many(
        self, items: list[tuple[str, Path]]
    ) -> dict[str, CaptionResult]:
        """并发生成，返回 ``image_id → 结果``。"""
        if not items:
            return {}
        semaphore = asyncio.Semaphore(self._concurrency)

        async def run(image_id: str, path: Path) -> CaptionResult:
            async with semaphore:
                return await self.caption_one(image_id, path)

        results = await asyncio.gather(*(run(image_id, path) for image_id, path in items))
        return {result.image_id: result for result in results}
