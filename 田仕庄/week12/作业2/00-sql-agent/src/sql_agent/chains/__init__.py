"""四条 LLM 链（原 openai-agents 的四个 Agent）。

工厂函数均为延迟构造：模块级只保留 PROMPT 常量，链在首次调用时创建并缓存。
这样在无 API key 的环境下 import 本包不会报错，离线测试得以保持。
"""

from __future__ import annotations

from .result_summarizer import get_result_summary_chain
from .sql_generator import get_sql_generator_chain
from .sql_rewriter import get_sql_rewriter_chain
from .table_selector import get_table_selector_chain

__all__ = [
    "get_result_summary_chain",
    "get_sql_generator_chain",
    "get_sql_rewriter_chain",
    "get_table_selector_chain",
]
