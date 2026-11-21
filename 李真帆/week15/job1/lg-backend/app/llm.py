"""大模型接入位置。

在 .env 填写 DEEPSEEK_API_KEY 即接入真实大模型（OpenAI 兼容接口，
DeepSeek / 通义 / vLLM 自部署均可，改 BASE_URL 和 CHAT_MODEL 即可）。
未配置时自动走规则兜底，保证整条链路可以跑通演示。
"""
import logging
import random

from openai import OpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_client: OpenAI | None = None

NEGATIVE_WORDS = ("难过", "伤心", "烦", "累", "焦虑", "压力", "崩溃", "失败", "挂科",
                  "报错", "孤独", "痛苦", "没用", "讨厌", "emo", "哭", "失眠", "被拒")
POSITIVE_WORDS = ("开心", "高兴", "快乐", "成功", "通过", "拿到", "顺利", "太好了",
                  "喜欢", "兴奋", "感谢", "中奖", "升职", "上岸", "哈哈")

ENCOURAGE_REPLIES = (
    "太棒了，继续保持这个状态，好事正在路上！",
    "为你开心！你的努力都被看见了，把这份好心情延续下去吧。",
    "很赞呀，趁着这股劲，下一个目标也稳了！",
)
COMFORT_REPLIES = (
    "辛苦了，遇到这种事谁都会难受，你已经做得很好了。",
    "别太苛责自己，先深呼吸，慢慢来，我陪着你。",
    "低谷只是暂时的，允许自己休息一下，再出发也不迟。",
)
NEUTRAL_REPLIES = (
    "收到～想多聊聊吗？我在听。",
    "好呀，不管是开心事还是烦心事，都可以跟我说说。",
)


def llm_configured() -> bool:
    return bool(settings.deepseek_api_key)


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url)
    return _client


def chat(system: str, user: str, *, temperature: float = 0.7, max_tokens: int = 200) -> str:
    """调用大模型（需先在 .env 配置 API key）。"""
    if not llm_configured():
        raise RuntimeError("DEEPSEEK_API_KEY 未配置，请在 .env 填写，或使用规则兜底")
    resp = _get_client().chat.completions.create(
        model=settings.chat_model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


def mock_sentiment(text: str) -> tuple[str, float]:
    """规则兜底：关键词判断情感，返回 (label, score)。"""
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    if neg > pos:
        return "negative", 0.9
    if pos > neg:
        return "positive", 0.9
    return "neutral", 0.3


def mock_reply(text: str, sentiment: str) -> str:
    """规则兜底：按情感从固定话术中选一句。"""
    pool = {"positive": ENCOURAGE_REPLIES, "negative": COMFORT_REPLIES}.get(sentiment, NEUTRAL_REPLIES)
    return random.choice(pool)
