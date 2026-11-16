"""识别资料库范围并将图文问题改写为文本检索词。"""

import json

from backend.app.agents.chat.models import ChatModelClient, RouteAndQuery
from backend.app.chat.domain.models import ChatImage
from backend.app.integrations.errors import ChatModelError


def scope_and_rewrite(
    question_text: str,
    user_image: ChatImage | None,
    corpus_profile: str,
    model: ChatModelClient,
    prompt: str,
) -> RouteAndQuery:
    """调用 Qwen3-VL 合并理解用户文本、可选图像和资料库主题概览。"""
    user_prompt = json.dumps(
        {"question": question_text, "corpus_profile": corpus_profile},
        ensure_ascii=False,
    )
    response = model.complete(prompt, user_prompt, [user_image] if user_image else [])
    try:
        values = json.loads(response)
    except (TypeError, json.JSONDecodeError) as error:
        raise ChatModelError("范围判断响应不是有效 JSON") from error
    if not isinstance(values, dict):
        raise ChatModelError("范围判断响应必须是 JSON 对象")
    route = values.get("route")
    search_query = values.get("search_query", "")
    direct_answer = values.get("direct_answer", "")
    if route not in {"in_scope", "out_of_scope"}:
        raise ChatModelError("范围判断 route 必须是 in_scope 或 out_of_scope")
    if not isinstance(search_query, str) or not isinstance(direct_answer, str):
        raise ChatModelError("范围判断响应字段类型无效")
    if route == "in_scope" and not search_query.strip():
        raise ChatModelError("范围内问题缺少 search_query")
    if route == "out_of_scope" and not direct_answer.strip():
        raise ChatModelError("范围外问题缺少 direct_answer")
    return RouteAndQuery(route, search_query.strip(), direct_answer.strip())
