"""配置管理：从环境变量 / .env 加载所有配置项。"""

import os
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env（若存在）
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

# ---------------------------------------------------------------------------
# LLM 配置（OpenAI 兼容接口，默认智谱 GLM）
# ---------------------------------------------------------------------------
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
# 主模型：生成/修正 SQL（质量关键路径）
LLM_MODEL = os.getenv("LLM_MODEL", "glm-4.5-air")
# 辅助模型：改写/评审/组织回答（可用更快的模型，留空则与主模型一致）
LLM_FAST_MODEL = os.getenv("LLM_FAST_MODEL", "")

# 思维链开关：auto（默认，GLM-4.5+ 推理模型自动关闭）/ enabled / disabled
#   - 智谱 GLM-4.5 系列默认开启深度思考，单次调用 10~20s；关闭后同质量下快 3~12 倍
#   - 非 GLM 推理模型（OpenAI/DeepSeek 等）不会注入该参数，避免请求报错
LLM_THINKING = os.getenv("LLM_THINKING", "auto").lower()
# 单次请求超时（秒）与最大重试次数（越小越快暴露问题，避免静默重试放大延迟）
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "1"))
# 后端：native（默认，httpx 直连，冷启动快约 2s）/ langchain（langchain_openai.ChatOpenAI）
LLM_BACKEND = os.getenv("LLM_BACKEND", "native").lower()

# ---------------------------------------------------------------------------
# 数据库配置
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///chinook.db")

# ---------------------------------------------------------------------------
# Agent 行为配置
# ---------------------------------------------------------------------------
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
MAX_RESULT_ROWS = int(os.getenv("MAX_RESULT_ROWS", "50"))
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "30"))
QUERY_TIMEOUT = float(os.getenv("QUERY_TIMEOUT", "10"))  # 单条 SQL 执行超时（秒）

# ---------------------------------------------------------------------------
# 评审回路（Reflection 模式）：write_query 后依次经过 改写优化 + 评审把关
# ---------------------------------------------------------------------------
ENABLE_QUERY_REVIEW = os.getenv("ENABLE_QUERY_REVIEW", "true").lower() in (
    "1", "true", "yes", "on",
)
# 改写策略：
#   on_revise（默认，省一次 LLM 调用）write → review →(revise) optimize → review
#   always   （兼容旧行为）          write → optimize → review
OPTIMIZE_STRATEGY = os.getenv("OPTIMIZE_STRATEGY", "on_revise").lower()
MAX_OPTIMIZE_ROUNDS = int(os.getenv("MAX_OPTIMIZE_ROUNDS", "2"))  # revise 回路上限

# ---------------------------------------------------------------------------
# 性能相关
# ---------------------------------------------------------------------------
# 是否流式输出回答（首 token 立即呈现，显著降低感知延迟）
STREAM_ANSWER = os.getenv("STREAM_ANSWER", "true").lower() in ("1", "true", "yes", "on")
# 表结构上下文里每张表的样例行数（0 = 不带样例，省 token 提速）
SCHEMA_SAMPLE_ROWS = int(os.getenv("SCHEMA_SAMPLE_ROWS", "2"))

# ---------------------------------------------------------------------------
# Web 服务配置（server.py，FastAPI + Vue 前端）
# ---------------------------------------------------------------------------
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
# 启动预热：预建 schema 缓存与 LLM 连接池，把首次请求的前置开销挪到启动阶段
WEB_WARMUP = os.getenv("WEB_WARMUP", "true").lower() in ("1", "true", "yes", "on")
# 问答缓存：Chinook 为静态只读库，相同问题（含相同会话历史、相同模式）
# 直接回放上次成功的 SSE 事件，跳过 LLM 往返。0 表示关闭。
WEB_CACHE_TTL = int(os.getenv("WEB_CACHE_TTL", "600"))
WEB_CACHE_SIZE = int(os.getenv("WEB_CACHE_SIZE", "64"))

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "logs/sql_agent.log")

# 项目根目录（定位 chinook.db 等资源）
PROJECT_ROOT = _ROOT


def resolve_sqlite_url(db_url: str) -> str:
    """将相对路径的 sqlite URL 解析为基于项目根目录的绝对路径 URL。

    保证无论从哪个工作目录启动，chinook.db 都能被正确找到。
    同时附加 mode=ro（只读）参数，作为安全纵深防御。
    格式参考 SQLAlchemy 官方文档：sqlite:///file:path?mode=ro&uri=true
    """
    if not db_url.startswith("sqlite:///"):
        return db_url
    raw = db_url[len("sqlite:///"):]
    # 剥离已有的 query 参数再重组
    path = raw.split("?", 1)[0]
    if not Path(path).is_absolute():
        path = str((PROJECT_ROOT / path).resolve())
    path = path.replace("\\", "/")
    return f"sqlite:///file:{path}?mode=ro&uri=true"
