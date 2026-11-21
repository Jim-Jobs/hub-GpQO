from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.models import PipelineResult


class PipelineAdapter(ABC):
    """编排引擎的应用层接口；服务层只认识它，不认识具体框架。"""

    name: str

    async def start(self) -> None:
        """建立引擎所需的连接或状态存储。"""

    @abstractmethod
    async def run(self, conversation_id: UUID, message: str) -> PipelineResult:
        """执行一次情感识别 + 回复生成。"""

    async def aclose(self) -> None:
        """释放引擎持有的资源。"""


if __name__ == "__main__":
    print(PipelineAdapter.__doc__)
    print([name for name in ("start", "run", "aclose") if hasattr(PipelineAdapter, name)])
