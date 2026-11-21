"""
Agent 定义层 —— 后端 B：OpenAI-Agents 服务

定义三个独立 Agent：
  - sentiment_agent   情感识别（结构化输出 SentimentOutput）    ← 进程 1（识别）
  - encourage_agent   生成鼓励话术（普通文本）                  ← 进程 2（生成 · 正向）
  - console_agent     生成排解话术（普通文本）                  ← 进程 2（生成 · 负向）

模型配置来自 .env，禁止硬编码。

【文件名说明】
规范最初写作 `agents.py`，但它与官方 `agents` SDK 包同名：从 agents_service/
目录启动服务时，`from agents import Agent` 会解析到本文件自身，导致循环导入。
因此本文件命名为 `local_agents.py`，彻底避开同名冲突，同时保持正常的静态 import。
"""

import os

from agents import (
    Agent,
    OpenAIChatCompletionsModel,
    set_default_openai_api,
    set_tracing_disabled,
)
from dotenv import load_dotenv
from openai import AsyncOpenAI

from schemas import SentimentOutput

# 显式指定本文件所在目录的 .env，保证从任意目录启动都能读到
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_SERVICE_DIR, ".env"))

# ---------------------------------------------------------------------------
# 全局配置（必须在定义 Agent 之前执行）
# ---------------------------------------------------------------------------
# 关闭 tracing（避免未配置 OpenAI 平台账号时产生警告 / 上报）
set_tracing_disabled(True)
# 使用标准的 /chat/completions 接口，SiliconFlow 兼容该协议
set_default_openai_api("chat_completions")

DEFAULT_MODEL = "deepseek-ai/DeepSeek-V4-Flash"

SILICONFLOW_API_KEY = os.getenv("SILICONFLOW_API_KEY", "").strip()
SILICONFLOW_MODEL = os.getenv("SILICONFLOW_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
SILICONFLOW_BASE_URL = os.getenv(
    "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
).strip()

if not SILICONFLOW_API_KEY:
    raise RuntimeError(
        "未检测到 SILICONFLOW_API_KEY，请在 agents_service/.env 中配置该环境变量。"
    )

# SiliconFlow 兼容的 OpenAI 客户端。
# 注意：Agents SDK 的 Runner.run_sync 内部会驱动一个事件循环，模型层需要异步客户端，
# 因此这里必须用 AsyncOpenAI（传同步的 OpenAI 客户端会报
# "object ChatCompletion can't be used in 'await' expression"）。
# 对外调用仍是同步的 Runner.run_sync，业务代码里没有 async/await。
siliconflow_client = AsyncOpenAI(
    api_key=SILICONFLOW_API_KEY,
    base_url=SILICONFLOW_BASE_URL,
)

# 三个 Agent 共用同一个模型对象，统一指向 SILICONFLOW_MODEL（缺省 DeepSeek-V4-Flash）
model = OpenAIChatCompletionsModel(
    model=SILICONFLOW_MODEL,
    openai_client=siliconflow_client,
)


# ---------------------------------------------------------------------------
# 进程 1（识别）：情感分类 Agent
# ---------------------------------------------------------------------------
sentiment_agent = Agent(
    name="sentiment_agent",
    instructions=(
        "你是一个情感分类器。判断用户文本的整体情感倾向是 positive（积极）还是 negative（消极）。"
        "只按结构化输出要求返回 sentiment 字段，不要额外解释。"
    ),
    model=model,
    output_type=SentimentOutput,  # 结构化输出
)


# ---------------------------------------------------------------------------
# 进程 2（生成 · 正向）：鼓励话术 Agent
# ---------------------------------------------------------------------------
encourage_agent = Agent(
    name="encourage_agent",
    instructions=(
        "你是一位温暖的情感陪伴助手。用户当前情绪积极，请针对用户分享的内容，"
        "用中文写一段真诚、具体的鼓励话术（2~4 句话），认可对方的感受并给予积极回应。"
        "不要提及情感分类，直接输出话术正文。"
    ),
    model=model,
)


# ---------------------------------------------------------------------------
# 进程 2（生成 · 负向）：排解话术 Agent
# ---------------------------------------------------------------------------
console_agent = Agent(
    name="console_agent",
    instructions=(
        "你是一位温柔、有同理心的情感陪伴助手。用户当前情绪消极，请针对用户分享的内容，"
        "用中文写一段安慰与排解的话术（2~4 句话）：先共情、接纳对方的情绪，"
        "再温和地给出一个可执行的小建议或换个视角的解读。"
        "不要说教，不要提及情感分类，也不要下诊断，直接输出话术正文。"
    ),
    model=model,
)
