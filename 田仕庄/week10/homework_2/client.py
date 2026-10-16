import asyncio
import logging
import os
import sys
from typing import Optional

from dotenv import load_dotenv
from agents import Agent, Runner, AsyncOpenAI, OpenAIChatCompletionsModel
from agents.mcp import MCPServer
from agents.mcp.server import MCPServerSse
from agents import set_default_openai_api, set_tracing_disabled

load_dotenv()

# ---------------- 日志 ----------------
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mcp-client")

set_default_openai_api("chat_completions")
set_tracing_disabled(True)

# ---------------- 运行时参数（从 .env 读取） ----------------
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "60"))
MAX_TURNS = int(os.getenv("MAX_TURNS", "10"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MCP_URL = os.getenv("MCP_SERVER_URL", "http://localhost:8000/sse")

# 曾经写死的 5 条测试消息，放到 --demo 里
DEMO_MESSAGES = [
    "最近有什么新闻？",
    "摇3次骰子得到结果。",
    "帮我查一下北京今天的天气。",
    "以『人工智能觉醒』为主题，帮我创作一篇短篇小说。",
    "介绍一下和秦始皇有关的历史事件。",
]


# ============================================================
# 构造 Agent
# ============================================================
def build_agent(mcp_server: MCPServer) -> Agent:
    external_client = AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ["OPENAI_BASE_URL"],
        timeout=REQUEST_TIMEOUT,          # 请求超时
        max_retries=MAX_RETRIES,          # SDK 内部退避重试
    )
    return Agent(
        name="Assistant",
        instructions=(
            "你是一个智能助手。根据用户的问题，选择合适的 MCP 工具完成任务；"
            "如果不需要工具，则直接回答。回答使用中文。"
        ),
        mcp_servers=[mcp_server],
        model=OpenAIChatCompletionsModel(
            model=os.getenv("OPENAI_MODEL", "deepseek-chat"),
            openai_client=external_client,
        ),
    )


# ============================================================
# 带重试的 ask（针对 MCP 侧偶发失败）
# ============================================================
async def ask(agent: Agent, message: str, history: Optional[list] = None):
    inputs = list(history) if history else []
    inputs.append({"role": "user", "content": message})

    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await Runner.run(
                starting_agent=agent,
                input=inputs,
                max_turns=MAX_TURNS,     # 防无限工具调用
            )
        except Exception as e:
            last_exc = e
            wait = min(2 ** attempt, 8)
            logger.warning("第 %d/%d 次调用失败：%s，%d 秒后重试",
                           attempt, MAX_RETRIES, e, wait)
            await asyncio.sleep(wait)
    logger.error("重试 %d 次后仍失败", MAX_RETRIES)
    raise last_exc  # type: ignore


# ============================================================
# 单次问答
# ============================================================
async def run_once(agent: Agent, message: str) -> None:
    try:
        result = await ask(agent, message)
        print(result.final_output)
    except Exception as e:
        logger.exception("处理失败：%s", e)


# ============================================================
# 交互模式（带多轮记忆）
# ============================================================
async def run_interactive(agent: Agent) -> None:
    history: list = []
    logger.info("进入交互模式，输入 exit / quit / q 退出")
    while True:
        try:
            text = await asyncio.to_thread(input, "\n>>> ")
        except (EOFError, KeyboardInterrupt):
            logger.info("退出。")
            break

        text = text.strip()
        if not text:
            continue
        if text.lower() in ("exit", "quit", "q"):
            logger.info("退出。")
            break

        try:
            result = await ask(agent, text, history)
            print(result.final_output)
            # 保留上下文，实现多轮对话
            history = result.to_input_list()
        except Exception as e:
            logger.exception("处理失败：%s", e)


# ============================================================
# 带断线重连的 MCP 会话
# ============================================================
async def connect_and_run(messages: list[str]) -> None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with MCPServerSse(
                name="SSE Python Server",
                params={
                    "url": MCP_URL,
                    "timeout": REQUEST_TIMEOUT,
                    "sse_read_timeout": REQUEST_TIMEOUT,
                },
                client_session_timeout_seconds=REQUEST_TIMEOUT,
            ) as server:
                agent = build_agent(server)
                if messages:
                    for m in messages:
                        logger.info("Running: %s", m)
                        await run_once(agent, m)
                else:
                    await run_interactive(agent)
                return
        except (ConnectionError, OSError) as e:
            wait = min(2 ** attempt, 8)
            logger.warning("连接 MCP Server 失败（第 %d/%d 次）：%s，%d 秒后重试",
                           attempt, MAX_RETRIES, e, wait)
            if attempt >= MAX_RETRIES:
                raise
            await asyncio.sleep(wait)


# ============================================================
# main
# ============================================================
async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        logger.error("未设置 OPENAI_API_KEY，请检查 .env")
        sys.exit(1)

    args = sys.argv[1:]
    if "--demo" in args:
        args.remove("--demo")
        messages = DEMO_MESSAGES
    else:
        messages = args   # 支持 python client.py "查北京天气"

    try:
        await connect_and_run(messages)
    except Exception as e:
        logger.exception("致命错误：%s", e)
        sys.exit(1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass