"""loguru 日志配置：控制台 + app.log（运行） + recognize.log（识别审计）。"""
import sys

from loguru import logger

from .config import settings

_FMT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "{name}:{function}:{line} | {message}"
)


def setup_logging() -> None:
    """初始化日志。幂等：重复调用先移除已有 handler。"""
    logger.remove()
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    # 控制台
    logger.add(sys.stderr, level="INFO", format=_FMT)

    # 全部运行日志，按天轮转
    logger.add(
        settings.log_dir / "app.log",
        level="DEBUG",
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        format=_FMT,
    )

    # 识别审计日志：仅记录带 audit=True 绑定的记录
    logger.add(
        settings.log_dir / "recognize.log",
        level="INFO",
        rotation="00:00",
        retention="90 days",
        encoding="utf-8",
        filter=lambda record: record["extra"].get("audit") is True,
        format=_FMT,
    )


def get_audit_logger():
    """识别审计专用 logger（写入 recognize.log）。"""
    return logger.bind(audit=True)
