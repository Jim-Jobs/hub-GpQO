"""日志模块测试。"""

import logging

from sql_agent.log import get_logger, setup_logging


def test_setup_logging_writes_file(tmp_path):
    """setup_logging 后，日志应同时写入文件。"""
    log_file = tmp_path / "test.log"
    logger = setup_logging(level="DEBUG", log_file=str(log_file))

    logger.info("hello-log-test")
    for h in logger.handlers:  # 强制刷新
        h.flush()

    content = log_file.read_text(encoding="utf-8")
    assert "hello-log-test" in content
    assert "INFO" in content


def test_setup_logging_idempotent():
    """重复调用不应累积 handler（避免日志重复输出）。"""
    logger1 = setup_logging()
    n_handlers = len(logger1.handlers)
    logger2 = setup_logging()
    assert logger1 is logger2
    assert len(logger2.handlers) == n_handlers


def test_get_logger_naming():
    """子 logger 应挂在 sql_agent 命名空间下。"""
    lg = get_logger("nodes")
    assert lg.name == "sql_agent.nodes"
    assert get_logger("sql_agent.x").name == "sql_agent.x"


def test_child_logger_inherits_level():
    """子 logger 应继承命名空间级别。"""
    setup_logging(level="WARNING")
    lg = get_logger("anything")
    assert lg.getEffectiveLevel() == logging.WARNING
