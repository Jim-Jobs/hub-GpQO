"""两套框架的运行画像：端口、Agent 地址、数据库路径、入口模块。

编排器不通过环境变量名去猜"自己是谁"，而是由入口模块显式传入画像，
这样两套框架可以同时运行而互不干扰。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backends.shared.config.settings import PROJECT_ROOT, get_settings


@dataclass(frozen=True)
class FrameworkProfile:
    """一个框架版本（LangGraph / OpenAI Agents SDK）的全部运行期参数。"""

    key: str
    label: str
    orchestrator_port: int
    agent1_port: int
    agent2_port: int
    agent1_url: str
    agent2_url: str
    database_path: str


def _resolve_db_path(raw: str) -> str:
    """把 ``./data/...`` 这类相对路径解析成基于项目根目录的绝对路径。"""
    path = Path(raw)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path)


def _build_profiles() -> dict[str, FrameworkProfile]:
    settings = get_settings()
    return {
        "langgraph": FrameworkProfile(
            key="langgraph",
            label="LangGraph",
            orchestrator_port=8000,
            agent1_port=8001,
            agent2_port=8002,
            agent1_url=settings.langgraph_agent1_url,
            agent2_url=settings.langgraph_agent2_url,
            database_path=_resolve_db_path(settings.langgraph_database_path),
        ),
        "openai_agents": FrameworkProfile(
            key="openai_agents",
            label="OpenAI Agents SDK",
            orchestrator_port=9000,
            agent1_port=9001,
            agent2_port=9002,
            agent1_url=settings.agents_sdk_agent1_url,
            agent2_url=settings.agents_sdk_agent2_url,
            database_path=_resolve_db_path(settings.agents_sdk_database_path),
        ),
    }


_PROFILES: dict[str, FrameworkProfile] | None = None


def get_profiles() -> dict[str, FrameworkProfile]:
    global _PROFILES
    if _PROFILES is None:
        _PROFILES = _build_profiles()
    return _PROFILES


def get_profile(key: str) -> FrameworkProfile:
    """按 ``langgraph`` / ``openai_agents`` 取运行画像。"""
    profiles = get_profiles()
    try:
        return profiles[key]
    except KeyError as exc:  # pragma: no cover - 配置错误才会触发
        raise KeyError(f"未知框架标识: {key!r}，可选: {sorted(profiles)}") from exc


#: ``(framework_key, service_name) -> (module_path, port)``，供启动脚本使用。
SERVICE_REGISTRY: dict[tuple[str, str], tuple[str, int]] = {
    ("langgraph", "orchestrator"): ("backends.langgraph.orchestrator.main:app", 8000),
    ("langgraph", "agent1"): ("backends.langgraph.agent1.main:app", 8001),
    ("langgraph", "agent2"): ("backends.langgraph.agent2.main:app", 8002),
    ("openai_agents", "orchestrator"): ("backends.openai_agents.orchestrator.main:app", 9000),
    ("openai_agents", "agent1"): ("backends.openai_agents.agent1.main:app", 9001),
    ("openai_agents", "agent2"): ("backends.openai_agents.agent2.main:app", 9002),
}
