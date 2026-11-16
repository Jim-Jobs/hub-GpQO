"""markdown 切块：移植参考项目逻辑（按行累积、超限开新块、过滤引用行）。

纯函数，便于单测。
"""

DEFAULT_CHUNK_SIZE = 256


def split_text2_chunks(lines: list[str], chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """把 markdown 行序列切成约 chunk_size 字符的块。

    规则（与参考项目一致）：
    - 跳过空行、"# References" 行、"[数字" 开头的引用行
    - 上一块未超 chunk_size 时继续累积（换行拼接），超限则开新块
    - 图片行 `![...](...)` 不做特殊处理，随所在块保留（图片信息随块入库）
    """
    chunks: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line == "# References":
            continue
        if len(line) > 2 and line[0] == "[" and line[1].isdigit():
            continue
        if not chunks:
            chunks.append(line)
        elif len(chunks[-1]) <= chunk_size:
            chunks[-1] += "\n" + line
        else:
            chunks.append(line)
    return chunks


def chunk_markdown(text: str, chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[str]:
    """入口：整篇 markdown 文本 -> chunk 列表。"""
    return split_text2_chunks(text.splitlines(), chunk_size)
