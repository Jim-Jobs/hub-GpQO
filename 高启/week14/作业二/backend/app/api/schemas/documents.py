"""文档上传和状态查询的 HTTP 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from backend.app.documents.domain.models import DocumentStatus


class DocumentStatusResponse(BaseModel):
    """前端上传后及轮询时使用的文档状态结构。"""

    model_config = ConfigDict(from_attributes=True)

    document_id: str
    file_name: str
    status: DocumentStatus
    progress: int
    error: str | None = None
    title: str | None = None
    summary: str | None = None
    parsed_at: datetime | None = None
