"""定义问答提交、轮询和确认接口的 JSON 响应模型。"""

from datetime import datetime

from pydantic import BaseModel

from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.chat.domain.models import ChatResponse


class ChatAcceptedResponse(BaseModel):
    """问答任务成功入队后立即返回的标识和状态。"""

    chat_id: str
    status: ChatStatus


class ChatStatusResponse(BaseModel):
    """前端轮询使用的状态、当前阶段、最终响应和安全错误摘要。"""

    chat_id: str
    status: ChatStatus
    stage: ChatStage
    created_at: datetime
    updated_at: datetime
    response: ChatResponse | None = None
    error: str | None = None
