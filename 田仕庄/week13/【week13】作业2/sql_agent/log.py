"""日志模块：统一配置 console + 滚动文件双通道日志。

用法：
    from sql_agent.log import setup_logging
    setup_logging()                 # 入口处调用一次（默认 INFO）
    logging.getLogger(__name__)     # 各模块正常取 logger，自动继承配置

配置（.env）：
    LOG_LEVEL   日志级别，默认 INFO（调试时改 DEBUG 可看到节点间状态流转）
    LOG_FILE    日志文件路径，默认 logs/sql_agent.log（滚动保留 3 份，每份 1MB）
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from sql_agent import config

_FMT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: str | None = None,
                  log_file: str | None = None) -> logging.Logger:
    """初始化 sql_agent 命名空间日志。可重复调用（幂等）。"""
    level = (level or config.LOG_LEVEL).upper()
    logger = logging.getLogger("sql_agent")
    logger.setLevel(getattr(logging, level, logging.INFO))
    logger.propagate = False

    # 幂等：重复调用时清掉旧 handler，避免重复输出
    for h in list(logger.handlers):
        logger.removeHandler(h)

    # 1) 控制台通道
    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
    logger.addHandler(console)

    # 2) 滚动文件通道
    log_file = log_file or config.LOG_FILE
    path = Path(log_file)
    if not path.is_absolute():
        path = config.PROJECT_ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        path, maxBytes=1 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter(_FMT, _DATE_FMT))
    logger.addHandler(file_handler)

    # 降低第三方库噪音
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    logger.debug("日志初始化完成: level=%s, file=%s", level, path)
    return logger


def get_logger(name: str) -> logging.Logger:
    """取 sql_agent.* 子 logger。"""
    if not name.startswith("sql_agent"):
        name = f"sql_agent.{name}"
    return logging.getLogger(name)
