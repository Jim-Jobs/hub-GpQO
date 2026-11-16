"""单次问答运行期间在显式 Python 节点间传递的状态。"""

from dataclasses import dataclass, field

from backend.app.chat.domain.models import ChatImage, ChatResponse
from backend.app.agents.chat.models import EvidenceAssessment, RetrievedChunk, RouteAndQuery


@dataclass
class ChatState:
    """保存用户输入、检索证据和本次运行得到的响应。"""

    question_text: str
    corpus_profile: str
    user_image: ChatImage | None = None
    route_and_query: RouteAndQuery | None = None
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    evidence: EvidenceAssessment | None = None
    response: ChatResponse | None = None
