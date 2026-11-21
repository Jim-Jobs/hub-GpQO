"""LangGraph 方案使用的 Chat Model（OpenAI 兼容）。"""

from functools import lru_cache

from langchain_openai import ChatOpenAI

from shared.config import get_api_key, get_base_url, get_model_name


@lru_cache(maxsize=1)
def get_chat_model() -> ChatOpenAI:
    kwargs = {
        "model": get_model_name(),
        "api_key": get_api_key(),
        "temperature": 0.3,
    }
    base_url = get_base_url()
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)
