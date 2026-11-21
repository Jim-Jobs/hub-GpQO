"""通过运行中的聊天 API 批量执行共享情感测试样例。"""

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4

from test.schemas import EvaluationRecord, TestCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = PROJECT_ROOT / "test" / "data" / "cases.jsonl"


def read_cases(path: Path) -> list[TestCase]:
    """读取并验证 JSONL 格式的标注样例。

    Args:
        path: 测试样例 JSONL 文件。

    Returns:
        按文件顺序排列的校验后样例。

    Raises:
        FileNotFoundError: 样例文件不存在。
        pydantic.ValidationError: 任一行不符合样例模型。
    """
    return [
        TestCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def submit_case(api_url: str, case: TestCase, timeout: int) -> dict[str, Any]:
    """提交一个样例并读取其 SSE 终态事件。

    Args:
        api_url: API 根地址，例如 `http://localhost:8000`。
        case: 当前待测标注样例。
        timeout: HTTP 请求超时秒数。

    Returns:
        API 返回的完成或失败事件字典。

    Raises:
        TimeoutError: 在超时时间内未收到终态事件。
        ValueError: API 返回了无效 JSON 或事件流格式。
        urllib.error.URLError: API 无法连接或请求失败。
    """
    request = Request(
        urljoin(f"{api_url.rstrip('/')}/", "api/chat"),
        data=json.dumps({"text": case.text}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        accepted = json.loads(response.read().decode("utf-8"))

    events_url = urljoin(f"{api_url.rstrip('/')}/", accepted["events_url"])
    with urlopen(Request(events_url), timeout=timeout) as response:
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8").rstrip("\r\n")
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif not line and data_lines:
                event = json.loads("\n".join(data_lines))
                data_lines.clear()
                if event.get("event_type") in {"completed", "failed"}:
                    return event
    raise TimeoutError(f"样例 {case.case_id} 的 SSE 未返回终态事件")


def evaluate_case(
    api_url: str,
    framework: str,
    case: TestCase,
    run_id: str,
    timeout: int,
) -> EvaluationRecord:
    """执行单条样例并转成共享评测记录。

    Args:
        api_url: 当前框架 API 根地址。
        framework: 实现名称，取 `langgraph` 或 `openai-agents`。
        case: 待测标注样例。
        run_id: 本次批量评测编号。
        timeout: 单条样例请求超时秒数。

    Returns:
        可直接写入结果 JSONL 的评测记录。

    Raises:
        urllib.error.URLError: API 连接或响应失败。
        ValueError: API 事件结构不符合预期。
    """
    started = time.perf_counter()
    try:
        event = submit_case(api_url, case, timeout)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if event["event_type"] == "completed":
            result = event["result"]
            return EvaluationRecord(
                case_id=case.case_id,
                run_id=run_id,
                framework=framework,
                status="completed",
                predicted_sentiment=result["sentiment"],
                format_valid=True,
                reply=result["reply"],
                latency_ms=elapsed_ms,
            )
        return EvaluationRecord(
            case_id=case.case_id,
            run_id=run_id,
            framework=framework,
            status="failed",
            latency_ms=elapsed_ms,
            error=f"{event.get('error_code', 'unknown')}: {event.get('message', '')}",
        )
    except Exception as error:
        return EvaluationRecord(
            case_id=case.case_id,
            run_id=run_id,
            framework=framework,
            status="failed",
            latency_ms=(time.perf_counter() - started) * 1000,
            error=type(error).__name__,
        )


def main() -> None:
    """解析参数、批量请求 API 并写出评测 JSONL。

    Args:
        无；API、框架、样例文件和输出路径通过命令行指定。

    Returns:
        所有样例处理完成后无返回值。

    Raises:
        SystemExit: 参数无效或文件不可写时退出。
    """
    parser = argparse.ArgumentParser(description="通过聊天 API 执行 30 条情感样例")
    parser.add_argument("--api-url", required=True, help="API 根地址，例如 http://localhost:8000")
    parser.add_argument("--framework", choices=("langgraph", "openai-agents"), required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    result_path = args.results or (
        PROJECT_ROOT / "test" / "results" / args.framework / f"run-{timestamp}.jsonl"
    )
    result_path.parent.mkdir(parents=True, exist_ok=True)
    cases = read_cases(args.cases)
    run_id = str(uuid4())
    with result_path.open("w", encoding="utf-8") as output:
        for index, case in enumerate(cases, start=1):
            record = evaluate_case(args.api_url, args.framework, case, run_id, args.timeout)
            output.write(record.model_dump_json() + "\n")
            print(f"[{index}/{len(cases)}] {case.case_id}: {record.status}")
    print(f"结果文件：{result_path}")


if __name__ == "__main__":
    main()
