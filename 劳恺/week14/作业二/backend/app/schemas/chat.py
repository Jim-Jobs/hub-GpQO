"""问答接口的出入参模型。

对应文档：``docs/04-API契约.md` §4。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class UserImage(BaseModel):
    """用户上传的图片（base64 data URL，不落盘）。"""

    data: str
    filename: str = ""


class ChatRequest(BaseModel):
    """``POST /api/chat`` 请求体。"""

    question: str
    """用户问题，**非空**。"""
    session_id: str | None = None
    """省略则新建会话；传了则延续历史（最近 3 轮）。"""
    images: list[UserImage] = Field(default_factory=list)
    """用户图片，最多 4 张。"""
    doc_ids: list[str] | None = None
    """限定检索范围；省略 = 全库。"""


class CitationPayload(BaseModel):
    image_id: str
    doc_id: str = ""
    doc_filename: str = ""
    page_no: int = 0
    url: str = ""
    caption: str = ""
    text: str = ""
    """``【图N】``"""


class ChatDelta(BaseModel):
    event: str = "delta"
    text: str


class ChatCitation(BaseModel):
    event: str = "citation"
    text: str
    image_id: str
    doc_id: str = ""
    doc_filename: str = ""
    page_no: int = 0
    url: str = ""
    caption: str = ""


class ChatDone(BaseModel):
    event: str = "done"
    session_id: str = ""
    answer: str = ""
    citations: list[CitationPayload] = Field(default_factory=list)
    retrieved_chunk_ids: list[str] = Field(default_factory=list)


class ChatError(BaseModel):
    event: str = "error"
    code: str = ""
    message: str = ""
