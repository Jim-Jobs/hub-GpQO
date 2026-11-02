"""节点进度文案（CLI 与 Web 共用的单一来源）。

`main.py`（REPL 逐节点打印）与 `server.py`（SSE node 事件的 label 字段）
都从这里引用，改文案只动这一个文件。
"""

from __future__ import annotations

NODE_LABELS: dict[str, str] = {
    "get_schema": "分析问题、匹配相关表结构",
    "write_query": "生成 SQL",
    "optimize_query": "专业化改写 SQL",
    "review_query": "评审 SQL 质量",
    "execute_query": "安全校验并执行",
    "rewrite_query": "根据错误修正 SQL（自动重试）",
    "generate_answer": "组织自然语言回答",
}
