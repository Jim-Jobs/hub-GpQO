from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    file_name: str
    file_size: int
    file_path: str
    description: str
    status: str
    error_msg: str
    page_count: int
    chunk_count: int
    created_at: str
    updated_at: str


class DocumentListOut(BaseModel):
    total: int
    items: list[DocumentOut]


class SessionOut(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str


class RetrievedRef(BaseModel):
    doc_name: str
    heading: str
    chunk_type: str
    score: float


class MessageOut(BaseModel):
    id: str
    session_id: str
    role: str
    content: str
    image_path: str
    meta: dict | None = None
    created_at: str
