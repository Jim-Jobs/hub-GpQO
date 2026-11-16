"""jieba 中文预分词。

对应文档：``docs/02-数据模型.md`` §2.2「``tokens`` 字段」。

入库时把文本切成词并用空格连接写进 ``chunks.tokens``，ES 用 ``standard`` 分词器即可按词检索。
这样**不需要 ``analysis-ik`` 插件**，规避"插件版本必须与 ES 版本严格一致"的部署坑。

jieba 首次 import 会加载词典（约 1s），因此惰性初始化 + 线程锁。
"""

from __future__ import annotations

import logging
import threading
import warnings
from typing import Any

logger = logging.getLogger(__name__)

_jieba_module: Any | None = None
_jieba_lock = threading.Lock()


def _ensure_jieba() -> Any:
    """惰性加载 jieba 模块（只初始化一次）。"""
    global _jieba_module
    if _jieba_module is None:
        with _jieba_lock:
            if _jieba_module is None:
                # jieba 内部仍依赖 pkg_resources，会触发弃用告警；此处定向屏蔽
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", message=".*pkg_resources is deprecated.*")
                    import jieba

                jieba.setLogLevel(logging.WARNING)
                jieba.initialize()
                _jieba_module = jieba
                logger.info("jieba 预分词器已初始化")
    return _jieba_module


def tokenize(text: str) -> str:
    """中文预分词 → 空格连接。

    纯标点 token 会被丢弃（对 BM25 是噪声）；英文与数字保留，
    ES 的 ``standard`` 分词器会自行做小写化。
    """
    if not text or not text.strip():
        return ""
    jieba = _ensure_jieba()
    tokens = [
        token.strip()
        for token in jieba.cut(text, cut_all=False)
        if token.strip() and any(ch.isalnum() for ch in token)
    ]
    return " ".join(tokens)


def tokenize_list(texts: list[str]) -> list[str]:
    """批量预分词，保持与输入一一对应。"""
    return [tokenize(text) for text in texts]
