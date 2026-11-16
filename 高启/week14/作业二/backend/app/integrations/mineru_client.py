"""MinerU 4.2.1 V1 Tasks API 的 HTTP 适配器。"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from typing import Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


class MineruApiError(RuntimeError):
    """MinerU 请求失败、任务终止或响应不符合 Tasks 契约。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        """保留稳定的错误摘要和是否可安全创建新解析任务。"""
        super().__init__(message)
        self.retryable = retryable


class MineruTransportError(MineruApiError):
    """HTTP 请求未收到可确认的服务端响应。"""


@dataclass(frozen=True)
class HttpResponse:
    """保存 HTTP 状态码和完整响应字节。"""

    status: int
    body: bytes


class HttpTransport(Protocol):
    """MinerU Tasks 客户端可替换的同步 HTTP 传输接口。"""

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        """发送一次请求并返回状态码与响应正文。"""


def _urlopen_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> HttpResponse:
    """使用 Python 标准库执行请求，并把 HTTP 错误作为普通响应返回。"""
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read())
    except HTTPError as error:
        return HttpResponse(error.code, error.read())


class MineruTasksClient:
    """按 MinerU 4.2.1 的 JSON Tasks 协议解析一个 PDF。"""

    _ACTIVE_STATES = {"waiting-file", "pending", "running"}

    def __init__(
        self,
        api_url: str,
        *,
        transport: HttpTransport = _urlopen_transport,
        request_timeout_seconds: float = 60,
        poll_interval_seconds: float = 2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """保存 API 基址和可替换的传输、计时依赖。"""
        self._api_url = api_url.rstrip("/")
        self._transport = transport
        self._request_timeout_seconds = request_timeout_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._clock = clock
        self._sleep = sleep

    def parse_pdf(self, pdf_path, *, timeout_seconds: float) -> bytes:
        """创建一次解析任务，按返回地址上传、轮询并下载 default ZIP。"""
        pdf_bytes = pdf_path.read_bytes()
        request_body = {
            "files": [
                {
                    "file_name": pdf_path.name,
                    "bytes": len(pdf_bytes),
                    "sha256sum": hashlib.sha256(pdf_bytes).hexdigest(),
                }
            ],
            "output_formats": ["default"],
        }

        # POST 创建操作没有客户端幂等键。响应丢失时结果可能已在服务端创建，
        # 因此明确报告不确定并停止，不能盲目重发以免重复占用解析资源。
        try:
            receipt = self._request_json(
                "POST",
                f"{self._api_url}/v1/tasks",
                request_body,
                expected_status={202},
            )
        except MineruTransportError as error:
            raise MineruApiError("MinerU 任务创建结果不确定，已停止自动重试") from error

        task_id, file_id = self._receipt_ids(receipt)
        deadline = self._clock() + timeout_seconds
        detail_url = f"{self._api_url}/v1/tasks/{quote(task_id, safe='')}"
        created_file = self._file_by_id(receipt, file_id)
        initial_upload = created_file.get("upload")
        if created_file.get("status") == "waiting-file" and isinstance(initial_upload, dict):
            self._upload_source(initial_upload, pdf_bytes)

        while True:
            try:
                detail = self._request_json("GET", detail_url)
            except MineruTransportError:
                # GET 不会创建新解析任务；短暂网络故障可以在总等待期限内安全重试。
                self._wait_until_next_poll(deadline)
                continue

            file_detail = self._file_by_id(detail, file_id)
            state = file_detail.get("status")
            if state == "completed":
                result_url = file_detail.get("result_url")
                if not isinstance(result_url, str) or not result_url:
                    raise MineruApiError("MinerU 完成响应缺少 result_url")
                return self._download_result(result_url)
            if state in {"failed", "canceled"}:
                error = file_detail.get("error", {})
                message = error.get("message") if isinstance(error, dict) else None
                code = error.get("code") if isinstance(error, dict) else None
                raise MineruApiError(
                    message or f"MinerU 任务终止，状态为 {state}",
                    retryable=code == "server_shutting_down",
                )
            if state not in self._ACTIVE_STATES:
                raise MineruApiError(f"MinerU 返回未知文件状态：{state!r}")

            upload = file_detail.get("upload")
            if state == "waiting-file" and isinstance(upload, dict):
                self._upload_source(upload, pdf_bytes)
            self._wait_until_next_poll(deadline)

    def _upload_source(self, upload: dict, pdf_bytes: bytes) -> None:
        """按服务端返回的原始 URL、请求头和 PUT 方法上传 PDF 字节。"""
        upload_url = upload.get("url")
        headers = upload.get("headers", {})
        if not isinstance(upload_url, str) or not isinstance(headers, dict):
            raise MineruApiError("MinerU 上传描述格式无效")
        self._request_bytes("PUT", upload_url, pdf_bytes, headers=headers)

    def _wait_until_next_poll(self, deadline: float) -> None:
        """在总等待期限内休眠一次，否则结束该解析任务。"""
        remaining = deadline - self._clock()
        if remaining <= 0:
            raise MineruApiError("MinerU 解析超过最大等待时间")
        self._sleep(min(self._poll_interval_seconds, remaining))

    def _download_result(self, result_url: str) -> bytes:
        """直接请求任务返回的结果地址，不擅自拼接服务端文件路径。"""
        return self._request_bytes("GET", result_url)

    def _request_json(
        self,
        method: str,
        url: str,
        payload: dict | None = None,
        *,
        expected_status: set[int] | None = None,
    ) -> dict:
        """发送 JSON 请求并验证状态码及对象格式。"""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        response = self._request_bytes(method, url, body, headers=headers, allow_error=True)
        accepted = expected_status or {200}
        if response.status not in accepted:
            code = self._error_code(response)
            retryable = method == "POST" and code == "server_shutting_down"
            raise MineruApiError(self._error_message(response), retryable=retryable)
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise MineruApiError("MinerU 返回无效 JSON") from error
        if not isinstance(decoded, dict):
            raise MineruApiError("MinerU JSON 响应必须是对象")
        return decoded

    def _request_bytes(
        self,
        method: str,
        url: str,
        body: bytes | None = None,
        *,
        headers: dict[str, str] | None = None,
        allow_error: bool = False,
    ) -> HttpResponse | bytes:
        """发送请求；只在解析 JSON 时将非成功状态交由调用方处理。"""
        try:
            response = self._transport(
                method,
                url,
                headers or {},
                body,
                self._request_timeout_seconds,
            )
        except (TimeoutError, OSError, URLError) as error:
            raise MineruTransportError(f"MinerU {method} 请求未收到响应") from error
        if not 200 <= response.status < 300 and not allow_error:
            raise MineruApiError(f"MinerU 请求失败，HTTP {response.status}")
        return response if allow_error else response.body

    @staticmethod
    def _error_message(response: HttpResponse) -> str:
        """尽量从 MinerU 错误对象提取稳定错误码和可读说明。"""
        try:
            error = json.loads(response.body.decode("utf-8")).get("error", {})
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            error = {}
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            if code or message:
                return f"MinerU 请求失败（{code or response.status}）：{message or '未知错误'}"
        return f"MinerU 请求失败，HTTP {response.status}"

    @staticmethod
    def _error_code(response: HttpResponse) -> str | None:
        """读取 MinerU ErrorDetail 的稳定 code，避免依赖可变错误文案。"""
        try:
            error = json.loads(response.body.decode("utf-8")).get("error", {})
        except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            return None
        code = error.get("code") if isinstance(error, dict) else None
        return code if isinstance(code, str) else None

    @staticmethod
    def _receipt_ids(receipt: dict) -> tuple[str, str]:
        """读取单文件任务回执中的服务端任务 ID 和文件 ID。"""
        task_id = receipt.get("task_id")
        files = receipt.get("files")
        if not isinstance(task_id, str) or not task_id or not isinstance(files, list) or len(files) != 1:
            raise MineruApiError("MinerU 创建回执必须包含 task_id 和一个文件")
        file_id = files[0].get("file_id") if isinstance(files[0], dict) else None
        if not isinstance(file_id, str) or not file_id:
            raise MineruApiError("MinerU 创建回执缺少 file_id")
        return task_id, file_id

    @staticmethod
    def _file_by_id(detail: dict, file_id: str) -> dict:
        """按服务端 file_id 关联状态，避免依赖回执顺序或文件名。"""
        files = detail.get("files")
        if not isinstance(files, list):
            raise MineruApiError("MinerU 任务详情缺少 files 数组")
        for item in files:
            if isinstance(item, dict) and item.get("file_id") == file_id:
                return item
        raise MineruApiError("MinerU 任务详情未返回创建回执中的 file_id")
