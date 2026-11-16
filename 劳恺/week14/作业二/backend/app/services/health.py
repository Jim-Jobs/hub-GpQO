"""依赖健康探测。

对应文档：``docs/04-API契约.md` §6 —— ``GET /healthz``。

约定：任一依赖 down 时整体 ``status`` 为 ``degraded``，但 HTTP 仍返回 200
（便于容器编排的存活探针）。
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

UP = "up"
DOWN = "down"


def parse_bootstrap_servers(servers: str) -> list[tuple[str, int]]:
    """把 ``host1:9092,host2:9092`` 解析成 ``[(host, port), ...]``。"""
    parsed: list[tuple[str, int]] = []
    for part in servers.split(","):
        entry = part.strip()
        if not entry:
            continue
        host, _, port = entry.partition(":")
        parsed.append((host or "localhost", int(port) if port else 9092))
    return parsed


async def _probe_http(url: str, timeout: float = 3.0) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            return response.status_code < 500
    except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
        return False


async def _probe_tcp(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
        return False


async def _probe_kafka(settings: Settings) -> bool:
    for host, port in parse_bootstrap_servers(settings.kafka_bootstrap_servers):
        if await _probe_tcp(host, port):
            return True
    return False


async def check_dependencies(settings: Settings) -> dict[str, str]:
    """探测各依赖的可达性，返回 ``{依赖名: "up"|"down"}``。"""
    # 桩实现跑在本进程内，天然可用，不需要探网络
    if settings.use_mock_llm:
        vllm_state = UP
    else:
        vllm_state = (
            UP
            if await _probe_http(f"{settings.llm_base_url.rstrip('/')}/models")
            else DOWN
        )

    if settings.mock_mode or settings.embedding_backend.lower() == "mock":
        embedding_state = UP
    else:
        embedding_state = (
            UP
            if await _probe_http(f"{settings.embedding_base_url.rstrip('/')}/models")
            else DOWN
        )

    elasticsearch_state = UP if await _probe_http(f"{settings.es_url.rstrip('/')}/") else DOWN
    kafka_state = UP if await _probe_kafka(settings) else DOWN

    return {
        "kafka": kafka_state,
        "elasticsearch": elasticsearch_state,
        "vllm": vllm_state,
        "embedding": embedding_state,
    }
