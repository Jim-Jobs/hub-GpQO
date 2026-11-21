"""Agent 服务 HTTP 客户端。

编排器通过 HTTP 调用 Agent 1 / Agent 2，两个 Agent 之间**不直接通信**。
"""

from __future__ import annotations

import logging

import httpx

from backends.shared.schemas.agent_api import AnalyzeRequest, RespondRequest
from backends.shared.schemas.emotion import EmotionResult
from backends.shared.schemas.response import ResponseResult

logger = logging.getLogger(__name__)


class AgentServiceError(RuntimeError):
    """Agent 服务不可用或返回了非法响应。"""

    def __init__(
        self,
        message: str,
        *,
        service: str = "",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.status_code = status_code


class AgentClient:
    """单个 Agent 服务的异步客户端。"""

    def __init__(
        self,
        base_url: str,
        service_name: str,
        timeout: float = 90.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_name = service_name
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout, connect=10.0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict) -> dict:
        try:
            response = await self._client.post(path, json=payload)
        except httpx.HTTPError as exc:
            raise AgentServiceError(
                f"调用 {self.service_name} 失败: {exc}", service=self.service_name
            ) from exc

        if response.status_code >= 400:
            raise AgentServiceError(
                f"{self.service_name} 返回 {response.status_code}: {response.text[:500]}",
                service=self.service_name,
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise AgentServiceError(
                f"{self.service_name} 返回的不是合法 JSON", service=self.service_name
            ) from exc

        if not isinstance(data, dict):
            raise AgentServiceError(
                f"{self.service_name} 返回的 JSON 不是对象", service=self.service_name
            )
        return data

    async def analyze(self, request: AnalyzeRequest) -> EmotionResult:
        """调用 Agent 1。"""
        data = await self._post("/analyze", request.model_dump(mode="json"))
        try:
            return EmotionResult.model_validate(data)
        except ValueError as exc:
            raise AgentServiceError(
                f"{self.service_name} 返回的情绪结果不合法: {data}", service=self.service_name
            ) from exc

    async def respond(self, request: RespondRequest) -> ResponseResult:
        """调用 Agent 2。"""
        data = await self._post("/respond", request.model_dump(mode="json"))
        try:
            return ResponseResult.model_validate(data)
        except ValueError as exc:
            raise AgentServiceError(
                f"{self.service_name} 返回的响应结果不合法: {data}", service=self.service_name
            ) from exc

    async def health(self) -> bool:
        """健康检查，失败返回 False 而不是抛异常。"""
        try:
            response = await self._client.get("/health", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200
