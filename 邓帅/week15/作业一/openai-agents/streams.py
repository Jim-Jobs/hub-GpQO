"""Workflow Streams 统一事件信封（worker publish / 网关 subscribe 共享）。

单一 topic "events" 上传输两类事件，durable log 的 offset 顺序保证
sentiment（workflow 在两 activity 之间发布）先于全部 delta
（respond activity 200ms 批次发布）。
"""

from __future__ import annotations

from dataclasses import dataclass

#: Workflow Streams 的主题名（worker 与网关必须一致）
EVENT_TOPIC = "events"


@dataclass
class StreamEvent:
    """流事件信封；type 区分语义，未用字段留空。"""

    type: str  # "sentiment" | "delta"
    label: str = ""
    reason: str = ""
    text: str = ""
