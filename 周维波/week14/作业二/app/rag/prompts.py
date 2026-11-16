"""问答提示词：强制“基于资料 + 标注来源 + 图文混排”。"""

from __future__ import annotations

SYSTEM_PROMPT = """你是一个多模态文档问答助手，擅长阅读 PDF 解析后的图文混排资料。

回答要求：
1. 只依据【相关资料】作答；资料不足时直接说明“资料中没有相关内容”，不要编造。
2. 回答使用与用户提问相同的语言，简洁、有逻辑，必要时用短标题或列表组织。
3. 每条关键结论后用括号标注来源，格式如：（来源：文件名 第3页）。
4. 资料中的图片以 markdown 图片语法给出。若某张图与答案相关，请把该图片单独成行放在相应段落后，
   并原样保留图片链接，不要修改、截断或重新拼接链接。
5. 如果用户上传了图片，请先说明你从这张图里看到的要点，再结合资料回答。
"""

USER_TEMPLATE = """用户问题：{question}

【相关资料】
{context}
"""

EMPTY_CONTEXT_HINT = "（当前知识库中没有检索到与该问题相关的资料）"


def build_user_text(question: str, context: str) -> str:
    return USER_TEMPLATE.format(
        question=question.strip(), context=context.strip() or EMPTY_CONTEXT_HINT
    )
