"""
作业2：在兼容 OpenAI Agent 的 MCP 服务上增加 3 个工具，并由 Agent 调用。

写法对齐本目录的 server.py / client.py：
- FastMCP 用 SSE 暴露工具
- Agent 通过 MCPServerSse 发现并调用这些工具

新增工具：
1. get_weather：查询城市天气
2. calculate：计算四则运算表达式
3. convert_length：长度单位换算
"""

import ast
import os
import operator
import socket
import threading
import time

os.environ["OPENAI_API_KEY"] = "sk-bf4240833ba44d6ea4e6b33dacd6be11"
os.environ["OPENAI_BASE_URL"] = "https://api.deepseek.com"

import asyncio
import datetime as _dt
from collections.abc import Callable

import httpx
from fastmcp import FastMCP
from mcp import ClientSession as _ClientSession
from openai import AsyncOpenAI

from agents import Agent, Runner, OpenAIChatCompletionsModel
from agents import set_default_openai_api, set_tracing_disabled
from agents.items import ToolCallItem, ToolCallOutputItem

# 当前环境里的 mcp 2.x 比 openai-agents 期望的 1.x 新，SSE 仍可用，先补上被改名的符号。
import mcp.client.streamable_http as _streamable_http
import mcp.shared.exceptions as _mcp_exceptions

if not hasattr(_streamable_http, "GetSessionIdCallback"):
    _streamable_http.GetSessionIdCallback = Callable[[], str | None]
if not hasattr(_streamable_http, "streamablehttp_client"):
    _streamable_http.streamablehttp_client = _streamable_http.streamable_http_client
if not hasattr(_mcp_exceptions, "McpError"):
    _mcp_exceptions.McpError = _mcp_exceptions.MCPError

# openai-agents 把读超时传成 timedelta，mcp 2.x 的 anyio.fail_after 只接受秒数。
_orig_session_init = _ClientSession.__init__


def _session_init(self, read_stream=None, write_stream=None, read_timeout_seconds=None, *args, **kwargs):
    if isinstance(read_timeout_seconds, _dt.timedelta):
        read_timeout_seconds = read_timeout_seconds.total_seconds()
    return _orig_session_init(self, read_stream, write_stream, read_timeout_seconds, *args, **kwargs)


_ClientSession.__init__ = _session_init

from agents.mcp.server import MCPServerSse

set_default_openai_api("chat_completions")
set_tracing_disabled(True)

HOST = "127.0.0.1"
PORT = 8001

mcp = FastMCP(name="Homework Tools")

_WEATHER = {
    "北京": "晴，18℃，东北风 3 级",
    "上海": "多云，22℃，东南风 2 级",
    "广州": "小雨，26℃，南风 2 级",
    "深圳": "阵雨，27℃，南风 3 级",
    "杭州": "阴，20℃，东风 2 级",
}


@mcp.tool
def get_weather(city: str) -> str:
    """查询指定城市的当前天气。city 为中文城市名，例如北京、上海。"""
    return _WEATHER.get(city, f"暂无{city}的天气数据，可查询：{'、'.join(_WEATHER)}")


_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_expr(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_expr(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_expr(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_expr(node.left), _eval_expr(node.right))
    raise ValueError("只支持数字和 + - * /")


@mcp.tool
def calculate(expression: str) -> str:
    """计算只含数字和加减乘除的表达式，例如 (12 + 8) * 3。"""
    try:
        value = _eval_expr(ast.parse(expression, mode="eval"))
    except Exception as exc:
        return f"无法计算：{exc}"
    if value.is_integer():
        return str(int(value))
    return str(value)


_TO_METER = {
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "km": 1000.0,
}


@mcp.tool
def convert_length(value: float, from_unit: str, to_unit: str) -> str:
    """长度换算。单位只能是 mm、cm、m、km。"""
    src = from_unit.strip().lower()
    dst = to_unit.strip().lower()
    if src not in _TO_METER or dst not in _TO_METER:
        return "单位必须是 mm、cm、m、km 之一"
    meters = value * _TO_METER[src]
    result = meters / _TO_METER[dst]
    text = str(int(result)) if float(result).is_integer() else f"{result:g}"
    return f"{value:g} {src} = {text} {dst}"


def _serve() -> None:
    mcp.run(transport="sse", host=HOST, port=PORT, show_banner=False)


def _wait_until_ready(timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex((HOST, PORT)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError(f"MCP 服务没有在 {HOST}:{PORT} 启动")


def _httpx_client_factory(headers=None, timeout=None, auth=None):
    """把 MCP 传入的超时转成当前 httpx 能识别的 Timeout。"""
    connect = getattr(timeout, "connect", None)
    read = getattr(timeout, "read", None)
    if connect is not None or read is not None:
        timeout = httpx.Timeout(connect or 5, read=read or 300)
    kwargs = {"follow_redirects": False}
    if timeout is not None:
        kwargs["timeout"] = timeout
    if headers is not None:
        kwargs["headers"] = headers
    if auth is not None:
        kwargs["auth"] = auth
    return httpx.AsyncClient(**kwargs)


def _print_tool_trace(result) -> None:
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            print(f"  调用工具: {item.tool_name}")
        elif isinstance(item, ToolCallOutputItem):
            print(f"  工具返回: {item.output}")


async def run_agent() -> None:
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
    )
    async with MCPServerSse(
        name="Homework MCP Server",
        params={
            "url": f"http://{HOST}:{PORT}/sse",
            # Agents 会把 httpx2.Timeout 交给 httpx，这里转成 httpx 自己的超时。
            "httpx_client_factory": _httpx_client_factory,
        },
        client_session_timeout_seconds=30,
    ) as mcp_server:
        agent = Agent(
            name="Assistant",
            instructions=(
                "你只能通过 MCP 工具回答问题，不要自己编造天气、计算结果或换算结果。"
                "查天气用 get_weather，算式用 calculate，长度换算用 convert_length。"
                "回答时直接给出工具返回的结果。"
            ),
            mcp_servers=[mcp_server],
            model=OpenAIChatCompletionsModel(
                model="deepseek-chat",
                openai_client=external_client,
            ),
        )

        questions = [
            "北京现在天气怎么样？",
            "帮我计算 (12 + 8) * 3",
            "把 2.5 km 换算成 m",
        ]
        for message in questions:
            print(f"\nRunning: {message}")
            result = await Runner.run(starting_agent=agent, input=message)
            _print_tool_trace(result)
            print(result.final_output)


def main() -> None:
    server = threading.Thread(target=_serve, daemon=True)
    server.start()
    _wait_until_ready()
    asyncio.run(run_agent())


if __name__ == "__main__":
    main()
