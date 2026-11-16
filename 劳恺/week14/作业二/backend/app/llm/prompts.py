"""图文交替 Prompt 组装。

对应文档：``docs/06-RAG与多模态设计.md` §5。

**核心原则：图片必须紧贴它所属的文字块，不能集中放在末尾。**

在多模态 message 里，图与它的说明文字**相邻**时，模型对"哪张图对应哪段话"的判断
准确率显著高于"图全在末尾 + 文本里用编号指代"。

图片顺序：**[用户图..., 文档图...]**，用户图在前。
``【图N】`` 的 N = 该图在本次送模图片列表中的 **1 基**序号。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: System prompt（docs/06 §5.4）。少样本示例能显著提高标记格式命中率。
SYSTEM_PROMPT = """你是一个严谨的多模态文档问答助手。请严格依据【参考资料】回答用户问题。

要求：
1. 只使用参考资料中的信息，不要凭记忆补充未出现的内容。
2. 当你引用某张图片时，必须在对应句子后插入 [[cite:{image_id}]] 标记，
   image_id 原样抄写我提供的 id（例如 [[cite:d_ab12_p7_i1]]），不要改写或编造。
3. 文字块后的【来源：《文件名》第 N 页】已经标明出处，回答时请保留这个来源信息。
4. 如果参考资料不足以回答，明确说明"资料中没有相关信息"，并指出还需要哪些信息。
5. 不要编造页码、图号或数据。
6. 回答用简体中文，结构清晰；涉及图表数据时请结合图片内容描述趋势与关键数值。

示例：
用户问：营收峰值在哪个季度？
回答：营收峰值出现在 2024 年第三季度【来源：《2024年度财报.pdf》第 7 页】[[cite:d_ab12_p7_i1]]，
      对应营收为 42.3 亿元。"""


@dataclass
class PromptImage:
    """一张送模图片。"""

    image_id: str
    """文档图的 ``image_id``；用户图用 ``user_<n>`` 之类的占位（不参与引用）。"""
    data_url: str
    page_no: int = 0
    caption: str = ""
    doc_filename: str = ""
    url: str = ""
    doc_id: str = ""
    is_user: bool = False
    chunk_id: str = ""
    """所属的文字块 —— 图必须紧贴它的文字。"""
    path: str = ""
    """相对 images_dir 的路径，用于加载磁盘文件。"""


@dataclass
class PromptChunk:
    """一个检索到的文字块。"""

    chunk_id: str
    text: str
    page_start: int = 0
    page_end: int = 0
    doc_filename: str = ""
    images: list[PromptImage] = field(default_factory=list)


@dataclass
class BuiltPrompt:
    """组装结果。"""

    messages: list[dict]
    image_order: list[PromptImage]
    """送模图片的顺序 —— ``【图N】`` 的 N 就是这个列表的 1 基下标。"""

    def index_of(self, image_id: str) -> int | None:
        for position, image in enumerate(self.image_order, start=1):
            if image.image_id == image_id:
                return position
        return None


def _source_label(chunk: PromptChunk) -> str:
    """块的来源标注（书名 + 页码）。"""
    name = chunk.doc_filename or "未知文档"
    if chunk.page_start and chunk.page_end:
        if chunk.page_start == chunk.page_end:
            return f"【来源：《{name}》第 {chunk.page_start} 页】"
        return f"【来源：《{name}》第 {chunk.page_start}-{chunk.page_end} 页】"
    return f"【来源：《{name}》】"


def build_chat_prompt(
    question: str,
    chunks: list[PromptChunk],
    user_images: list[PromptImage] | None = None,
    history: list[dict] | None = None,
) -> BuiltPrompt:
    """组装图文交替的 messages。

    ``history`` 是最近几轮的原始消息（``{"role": ..., "content": ...}``），
    **历史图片不重传**（docs/06 §7）。
    """
    user_images = user_images or []
    history = history or []

    content: list[dict] = []

    # ① 用户图片排最前 —— 前端的 【图1】【图2】 对应用户图
    for image in user_images:
        content.append({"type": "image_url", "image_url": {"url": image.data_url}})

    content.append({"type": "text", "text": f"用户问题：{question}\n\n"})

    # ② 文档图片：标记 → 图 → 紧邻的文字
    for chunk in chunks:
        if chunk.images:
            for image in chunk.images:
                content.append(
                    {
                        "type": "text",
                        "text": f'<img id="{image.image_id}" page="{image.page_no}" '
                        f'caption="{_escape_attr(image.caption)}">',
                    }
                )
                content.append({"type": "image_url", "image_url": {"url": image.data_url}})
                content.append(
                    {
                        "type": "text",
                        "text": f"{_source_label(chunk)}\n{chunk.text}",
                    }
                )
                # 一块有多张图时，后续图片不再重复正文
                chunk = PromptChunk(
                    chunk_id=chunk.chunk_id,
                    text="（同上页图片）",
                    page_start=chunk.page_start,
                    page_end=chunk.page_end,
                    doc_filename=chunk.doc_filename,
                )
        else:
            content.append({"type": "text", "text": f"{_source_label(chunk)}\n{chunk.text}"})

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": content})

    image_order = [*user_images]
    for chunk in chunks:
        image_order.extend(chunk.images)

    return BuiltPrompt(messages=messages, image_order=image_order)


def _escape_attr(value: str) -> str:
    """转义放进 HTML 属性里的引号与换行，避免破坏 Prompt 结构。"""
    return value.replace('"', "'").replace("\n", " ").replace("\r", " ")
