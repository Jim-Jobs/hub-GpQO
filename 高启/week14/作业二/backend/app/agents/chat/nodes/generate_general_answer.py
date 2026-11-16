"""生成用户确认后的通用回答，不查询 PDF 资料或添加文档引用。"""

import json

from backend.app.agents.chat.models import ChatModelClient
from backend.app.chat.domain.models import ChatImage, ChatResponse
from backend.app.integrations.errors import ChatModelError


def generate_general_answer(
    question_text: str,
    user_image: ChatImage | None,
    model: ChatModelClient,
    prompt: str,
) -> ChatResponse:
    """将重传的问题/图片交给模型，并解析不含 PDF 来源的回答文本。"""
    user_prompt = json.dumps({"question": question_text}, ensure_ascii=False)
    response = model.complete(prompt, user_prompt, [user_image] if user_image else [])
    try:
        values = json.loads(response)
    except (TypeError, json.JSONDecodeError) as error:
        raise ChatModelError("通用回答响应不是有效 JSON") from error
    if not isinstance(values, dict) or not isinstance(values.get("text"), str):
        raise ChatModelError("通用回答响应缺少 text 字符串")
    answer = values["text"].strip()
    if not answer:
        raise ChatModelError("通用回答内容为空")
    return ChatResponse("general", [{"type": "text", "text": answer}])
