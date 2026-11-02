"""DeepSeek-V4.1-Flash 多模态调用与识别编排（调用 -> 解析 -> 兜底重试）。"""
import time

from loguru import logger
from openai import APIConnectionError, APIError, APITimeoutError, OpenAI

from ..classifier import ClassificationError, ClassificationResult, parse_model_output
from ..config import settings
from ..prompt import RISK_PROMPT, SYSTEM_PROMPT


class VisionServiceError(Exception):
    """模型服务调用失败。"""


class VisionService:
    def __init__(self) -> None:
        if not settings.deepseek_api_key:
            raise VisionServiceError("未配置 DEEPSEEK_API_KEY，请在 .env 中设置")
        self._client = OpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.request_timeout,
        )

    def _call_model(self, image_base64: str, mime_type: str) -> str:
        """调用 deepseek-flash，返回原始文本。"""
        data_url = f"data:{mime_type};base64,{image_base64}"
        try:
            resp = self._client.chat.completions.create(
                model=settings.model_name,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": RISK_PROMPT},
                            {"type": "image_url", "image_url": {"url": data_url}},
                        ],
                    },
                ],
                temperature=0.0,
                max_tokens=4096,  # deepseek-flash 为思考型模型，需为 reasoning + JSON 输出留足 token
            )
        except (APITimeoutError, APIConnectionError) as exc:
            raise VisionServiceError(f"模型服务连接失败: {exc}") from exc
        except APIError as exc:
            raise VisionServiceError(f"模型服务返回错误: {exc}") from exc

        content = (resp.choices[0].message.content or "").strip()
        logger.debug("模型原始输出: {}", content)
        return content

    def recognize(self, image_base64: str, mime_type: str) -> tuple[ClassificationResult, int]:
        """
        识别入口：调用模型并解析，解析失败按 settings.max_retries 重试。
        返回 (ClassificationResult, latency_ms)。
        """
        start = time.perf_counter()
        last_error: Exception | None = None

        for attempt in range(settings.max_retries + 1):
            raw = self._call_model(image_base64, mime_type)
            try:
                result = parse_model_output(raw)
                latency_ms = int((time.perf_counter() - start) * 1000)
                if attempt > 0:
                    logger.info("第 {} 次重试后解析成功", attempt)
                return result, latency_ms
            except ClassificationError as exc:
                last_error = exc
                logger.warning("第 {} 次尝试解析失败: {}", attempt + 1, exc)

        raise ClassificationError(f"重试 {settings.max_retries} 次后仍无法解析: {last_error}")
