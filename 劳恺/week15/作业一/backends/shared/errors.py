"""跨框架共享的异常类型。"""

from __future__ import annotations


class WorkflowError(RuntimeError):
    """Agent 工作流执行失败。

    例如模型不可用（缺 API Key、鉴权失败、限流、网络问题）。
    工作流层只抛这个异常，由各服务的 ``main.py`` 统一转换成 HTTP 502，
    这样两套框架的 HTTP 行为保持一致。
    """
