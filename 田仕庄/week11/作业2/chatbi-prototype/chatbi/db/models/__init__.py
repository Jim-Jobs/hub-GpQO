"""模型注册入口：import 本模块即完成全部表的 metadata 注册。

``embedding_pg`` 依赖 pgvector，不在此导入。
"""
from .org import AppUser, DataSource, Role, Tenant, UserRole
from .semantic import SemanticEvent, SemanticObject, SemanticRelease
from .permission import PermissionRule
from .chat import ChatClarify, ChatSession, ChatTurn
from .audit import QueryAudit
from .eval import EvalCase, EvalResult, EvalRun, Feedback
from .recall import SemanticTerm

__all__ = [
    "Tenant", "AppUser", "Role", "UserRole", "DataSource",
    "SemanticRelease", "SemanticObject", "SemanticEvent",
    "PermissionRule",
    "ChatSession", "ChatTurn", "ChatClarify",
    "QueryAudit",
    "EvalCase", "EvalRun", "EvalResult", "Feedback",
    "SemanticTerm",
]
