"""引用协议：``[[cite:<image_id>]]`` 的流式识别与降级。

对应文档：``docs/06-RAG与多模态设计.md` §6。

**核心难点：标记会跨 token 断裂。**

```
delta1: "…出现在 2024 年第三季度[[ci"
delta2: "te:d_ab12_p7_i1]]，对应营收…"
```

若直接转发，前端会看到半截标记。所以维护一个缓冲区，**扣住疑似标记前缀的尾部**。

**降级原则：宁可多显示一点原始文本，也不丢内容、不报错。**

* 标记里的 image_id 不在本次送模列表 → 原样保留 ``[[cite:xxx]]``，不产生 citation 事件
* 整条回答没有任何标记 → 正常输出；``done.citations`` 退化为"本次检索到的全部图片"
* 模型输出了乱格式（如 ``[图1]``）→ 当普通文本输出，不报错
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 标记前缀。缓冲区里以它开头的尾巴必须扣住
MARKER_PREFIX = "[[cite:"
#: 完整标记。image_id 字符集见 docs/06 §6.1
CITE_PATTERN = re.compile(r"\[\[cite:([A-Za-z0-9_\-:.]+)\]\]")
#: 疑似标记的最大长度。超过就不是标记了，应当放行（防止一个孤立的 "[" 卡住整段输出）
MAX_MARKER_LENGTH = 256


@dataclass
class Citation:
    """一次成功的引用。"""

    image_id: str
    index: int
    """``【图N】`` 中的 N —— 该图在本次送模图片列表中的 1 基序号。"""
    text: str
    """``【图N】``"""
    url: str = ""
    doc_id: str = ""
    doc_filename: str = ""
    page_no: int = 0
    caption: str = ""


@dataclass
class CitationEvent:
    """``citation`` SSE 事件载荷（docs/04 §4）。"""

    text: str
    image_id: str
    doc_id: str = ""
    doc_filename: str = ""
    page_no: int = 0
    url: str = ""
    caption: str = ""


@dataclass
class ImageMeta:
    """送模图片的元信息。用于把 ``image_id`` 翻译成 ``【图N】`` 与引用卡片。"""

    image_id: str
    index: int
    url: str = ""
    doc_id: str = ""
    doc_filename: str = ""
    page_no: int = 0
    caption: str = ""


class CitationParser:
    """流式引用解析器。

    用法::

        parser = CitationParser(images)   # images: list[ImageMeta]
        safe_text, citations = parser.feed(delta)
        ...
        tail_text, citations = parser.flush()   # 流结束
    """

    def __init__(self, images: list[ImageMeta]) -> None:
        self._images = {image.image_id: image for image in images}
        self._pending = ""
        self._cited: dict[str, Citation] = {}
        self._order: list[str] = []

    @property
    def citations(self) -> list[Citation]:
        """已识别的引用，按首次出现顺序（去重保序）。"""
        return [self._cited[key] for key in self._order]

    def feed(self, text: str) -> tuple[str, list[CitationEvent]]:
        """喂入增量文本，返回 ``(可安全输出的文本, 本次新识别到的引用)``。"""
        self._pending += text
        return self._drain(final=False)

    def flush(self) -> tuple[str, list[CitationEvent]]:
        """流结束：把缓冲区里剩下的内容全部吐出（可能是半个标记，原样保留）。"""
        return self._drain(final=True)

    def _drain(self, *, final: bool) -> tuple[str, list[CitationEvent]]:
        safe: list[str] = []
        events: list[CitationEvent] = []
        buffer = self._pending
        position = 0

        while True:
            match = CITE_PATTERN.search(buffer, position)
            if match is None:
                break
            image_id = match.group(1)
            meta = self._images.get(image_id)
            if meta is None:
                # 未知的 image_id → 原样保留（可读降级）
                safe.append(buffer[position : match.end()])
            else:
                safe.append(buffer[position : match.start()])
                safe.append(f"【图{meta.index}】")
                if image_id not in self._cited:
                    citation = Citation(
                        image_id=image_id,
                        index=meta.index,
                        text=f"【图{meta.index}】",
                        url=meta.url,
                        doc_id=meta.doc_id,
                        doc_filename=meta.doc_filename,
                        page_no=meta.page_no,
                        caption=meta.caption,
                    )
                    self._cited[image_id] = citation
                    self._order.append(image_id)
                events.append(
                    CitationEvent(
                        text=f"【图{meta.index}】",
                        image_id=image_id,
                        doc_id=meta.doc_id,
                        doc_filename=meta.doc_filename,
                        page_no=meta.page_no,
                        url=meta.url,
                        caption=meta.caption,
                    )
                )
            position = match.end()

        rest = buffer[position:]
        # hold 是"扣住部分"的起始下标：返回 len(rest) 表示不扣任何内容
        hold = len(rest) if final else self._hold_start(rest)
        safe.append(rest[:hold])
        self._pending = rest[hold:]
        return "".join(safe), events

    @staticmethod
    def _hold_start(rest: str) -> int:
        """返回"扣住"部分的起始下标。返回 ``len(rest)`` 表示不扣任何内容。

        只扣住**可能是标记前缀**的尾巴：

        * 以 ``[[`` 开头的尾巴 —— 标记进行中；
        * 缓冲区**正好以单个 ``[`` 结尾** —— 可能是被拆开的 ``[[``（下一个 token 补上）。

        其它情况（例如正文里的 ``数组 a[0]``）立即放行，避免一个孤立的 ``[``
        卡住整段输出。
        """
        if not rest:
            return 0

        # 注意必须找 "[[" 而不是 "[" —— rfind("[") 会命中第二个括号，tail 就成了 "[ci"
        index = rest.rfind("[[")
        if index >= 0:
            tail = rest[index:]
            if MARKER_PREFIX.startswith(tail):
                return index  # tail 是标记前缀的一部分
            if tail.startswith(MARKER_PREFIX):
                if "]]" in tail:
                    return len(rest)  # 已经被正则处理过了
                if len(tail) > MAX_MARKER_LENGTH:
                    return len(rest)  # 太长，不可能是标记
                return index  # 扣住，等待后续 token
            return len(rest)

        # 缓冲区正好以单个 "[" 结尾 → 可能是 "[[" 被拆开了
        if rest.endswith("["):
            return len(rest) - 1
        return len(rest)


@dataclass
class CitationState:
    """解析器之外要保留的状态（供 ``done`` 事件组装）。"""

    answer: str = ""
    citations: list[Citation] = field(default_factory=list)
    had_marker: bool = False
    """回答里是否出现过至少一个标记。用来判断要不要走降级。"""
