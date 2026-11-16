"""问答应用服务依赖的状态存储协议。"""

from typing import Protocol

from backend.app.chat.domain.models import ChatStatusRecord


class ChatStatusStore(Protocol):
    """描述问答任务状态仓库需要提供的持久化操作。"""

    def save(self, record: ChatStatusRecord) -> None:
        """按 chat_id 保存问答状态并刷新过期时间。"""

    def get(self, chat_id: str) -> ChatStatusRecord | None:
        """读取单个问答状态；未知 ID 返回 None。"""

    def fail_interrupted(self) -> int:
        """将上次进程遗留的 accepted/running 状态标记为失败。"""
