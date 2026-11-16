"""MinerU 4.2.1 Tasks API 客户端测试。"""

import json
import zipfile
from io import BytesIO

import pytest

from backend.app.integrations.mineru_client import MineruApiError, MineruTasksClient


class FakeResponse:
    """保存一次模拟 HTTP 请求的状态码和响应正文。"""

    def __init__(self, status: int, body: bytes) -> None:
        """初始化可被 MinerU 客户端读取的响应。"""
        self.status = status
        self.body = body

    def read(self) -> bytes:
        """返回完整响应正文。"""
        return self.body


class FakeTransport:
    """按队列返回响应，并保留请求信息供断言。"""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        """保存客户端依次收到的模拟 HTTP 结果。"""
        self.responses = responses
        self.requests: list[tuple[str, str, dict, bytes | None]] = []

    def __call__(self, method: str, url: str, headers: dict, body: bytes | None, timeout: float):
        """记录请求并返回下一项模拟结果。"""
        self.requests.append((method, url, headers, body))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _json_response(payload: dict, status: int = 200) -> FakeResponse:
    """将字典编码为 MinerU JSON 响应。"""
    return FakeResponse(status, json.dumps(payload).encode("utf-8"))


def _result_zip() -> bytes:
    """构造包含 4.2.1 default 产物的最小 ZIP。"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("structured_content.json", '{"pages": [{"page_idx": 0, "blocks": []}]}')
        archive.writestr("markdown.md", "# 解析结果")
    return output.getvalue()


def test_tasks_client_uploads_polls_and_downloads_default_result(tmp_path) -> None:
    """验证 4.2.1 的 JSON 创建、PUT 上传、状态轮询和 result_url 下载顺序。"""
    pdf_path = tmp_path / "报告.pdf"
    pdf_path.write_bytes(b"%PDF-test-data")
    result_zip = _result_zip()
    transport = FakeTransport(
        [
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "waiting-file",
                    "files": [
                        {
                            "file_id": "file-1",
                            "file_name": "报告.pdf",
                            "status": "waiting-file",
                            "upload": {"url": "http://mineru/upload", "headers": {"X-Test": "yes"}},
                        }
                    ],
                },
                status=202,
            ),
            FakeResponse(200, b"uploaded"),
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "completed",
                    "files": [
                        {
                            "file_id": "file-1",
                            "file_name": "报告.pdf",
                            "status": "completed",
                            "result_url": "http://mineru/result.zip",
                        }
                    ],
                }
            ),
            FakeResponse(200, result_zip),
        ]
    )

    result = MineruTasksClient(
        "http://127.0.0.1:8000/api-prefix/", transport=transport, poll_interval_seconds=0
    ).parse_pdf(pdf_path, timeout_seconds=1)

    assert result == result_zip
    assert [request[0] for request in transport.requests] == ["POST", "PUT", "GET", "GET"]
    assert transport.requests[0][1] == "http://127.0.0.1:8000/api-prefix/v1/tasks"
    create_payload = json.loads(transport.requests[0][3])
    assert create_payload["files"][0]["file_name"] == "报告.pdf"
    assert create_payload["files"][0]["bytes"] == len(b"%PDF-test-data")
    assert create_payload["output_formats"] == ["default"]
    assert transport.requests[1][2] == {"X-Test": "yes"}
    assert transport.requests[1][3] == b"%PDF-test-data"
    assert transport.requests[2][1].endswith("/v1/tasks/task-1")
    assert transport.requests[3][1] == "http://mineru/result.zip"


def test_ambiguous_task_creation_failure_is_not_retried(tmp_path) -> None:
    """创建请求超时可能已被 MinerU 接收，客户端不得再次 POST。"""
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-test-data")
    transport = FakeTransport([TimeoutError("response lost")])

    with pytest.raises(MineruApiError, match="任务创建结果不确定"):
        MineruTasksClient("http://127.0.0.1:8000", transport=transport).parse_pdf(
            pdf_path, timeout_seconds=1
        )

    assert len(transport.requests) == 1
    assert transport.requests[0][0] == "POST"


def test_failed_task_returns_error_without_creating_another_task(tmp_path) -> None:
    """MinerU 返回文件失败时抛出终止错误，不自动新建重复任务。"""
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-test-data")
    transport = FakeTransport(
        [
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "pending",
                    "files": [{"file_id": "file-1", "file_name": "report.pdf", "status": "pending"}],
                },
                status=202,
            ),
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "failed",
                    "files": [
                        {
                            "file_id": "file-1",
                            "file_name": "report.pdf",
                            "status": "failed",
                            "error": {"code": "parse_failed", "message": "解析失败"},
                        }
                    ],
                }
            ),
        ]
    )

    with pytest.raises(MineruApiError, match="解析失败"):
        MineruTasksClient(
            "http://127.0.0.1:8000", transport=transport, poll_interval_seconds=0
        ).parse_pdf(pdf_path, timeout_seconds=1)

    assert [request[0] for request in transport.requests] == ["POST", "GET"]


def test_task_polling_stops_at_configured_deadline(tmp_path) -> None:
    """任务超过最长等待时间后返回明确错误，不无限占用 Kafka consumer。"""
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-test-data")
    transport = FakeTransport(
        [
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "pending",
                    "files": [{"file_id": "file-1", "file_name": "report.pdf", "status": "pending"}],
                },
                status=202,
            ),
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "running",
                    "files": [{"file_id": "file-1", "file_name": "report.pdf", "status": "running"}],
                }
            ),
        ]
    )

    with pytest.raises(MineruApiError, match="超过最大等待时间"):
        MineruTasksClient(
            "http://127.0.0.1:8000", transport=transport, poll_interval_seconds=0
        ).parse_pdf(pdf_path, timeout_seconds=0)

    assert len(transport.requests) == 2


def test_failed_result_download_does_not_submit_a_second_parse_task(tmp_path) -> None:
    """结果下载失败时复用已完成任务失败，不创建另一个解析任务。"""
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-test-data")
    transport = FakeTransport(
        [
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "pending",
                    "files": [{"file_id": "file-1", "file_name": "report.pdf", "status": "pending"}],
                },
                status=202,
            ),
            _json_response(
                {
                    "task_id": "task-1",
                    "status": "completed",
                    "files": [
                        {
                            "file_id": "file-1",
                            "file_name": "report.pdf",
                            "status": "completed",
                            "result_url": "http://mineru/result.zip",
                        }
                    ],
                }
            ),
            FakeResponse(503, b"temporarily unavailable"),
        ]
    )

    with pytest.raises(MineruApiError, match="HTTP 503"):
        MineruTasksClient("http://127.0.0.1:8000", transport=transport).parse_pdf(
            pdf_path, timeout_seconds=1
        )

    assert [request[0] for request in transport.requests] == ["POST", "GET", "GET"]


def test_explicit_server_shutdown_rejection_can_use_worker_retry_policy(tmp_path) -> None:
    """只有明确拒绝接单的临时服务状态可进入外层有限重试。"""
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-test-data")
    transport = FakeTransport(
        [
            FakeResponse(
                503,
                b'{"error":{"code":"server_shutting_down","message":"restarting"}}',
            )
        ]
    )

    with pytest.raises(MineruApiError) as caught:
        MineruTasksClient("http://127.0.0.1:8000", transport=transport).parse_pdf(
            pdf_path, timeout_seconds=1
        )

    assert caught.value.retryable is True
    assert len(transport.requests) == 1
