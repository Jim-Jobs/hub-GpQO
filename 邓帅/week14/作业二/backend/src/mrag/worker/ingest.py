"""入库：mineru 产物 markdown -> 切块 -> 三路向量 -> Zilliz。

幂等：每次入库先按 db_id 清旧向量（重试/重解析不产生重复 chunk）。
修复参考项目 bug：图片路径用 mrag.paths 的 os.path.join 解析，文件存在才
编码真实 CLIP 图像向量，否则零向量。
"""

from pathlib import Path

from sqlalchemy.orm import Session

from mrag import zilliz_store
from mrag.chunker import chunk_markdown
from mrag.config import PROCESSED_DIR
from mrag.embeddings import (
    encode_image_clip,
    encode_text_bge,
    encode_text_clip,
    zero_clip_vector,
)
from mrag.models import STATUS_DONE, FileRow
from mrag.paths import rewrite_chunk_images


def strip_image_lines(chunk_text: str) -> str:
    """去掉图片行后的纯文本（用于文本向量编码，图片路径是噪音）。"""
    return "\n".join(ln for ln in chunk_text.splitlines() if not ln.lstrip().startswith("!["))


def build_rows(db_id: int, file_name: str, chunks: list[str], markdown_path: Path) -> list[dict]:
    rows = []
    for chunk in chunks:
        # 入库前把图片相对路径重写为 /files/... URL：检索端与 LLM 直接可用
        rewritten, local_images = rewrite_chunk_images(chunk, markdown_path, PROCESSED_DIR)

        text_only = strip_image_lines(rewritten) or rewritten
        text_vec = encode_text_bge(text_only)
        clip_text_vec = encode_text_clip(text_only)

        # 含图 chunk：取第一张本地存在的图片编码 CLIP 图像向量（与参考一致）
        img_vec = zero_clip_vector()
        for abs_p in local_images:
            v = encode_image_clip(str(abs_p))
            if v is not None:
                img_vec = v
                break

        rows.append(
            {
                "db_id": db_id,
                "file_name": file_name,
                "text": rewritten[:65000],
                "text_vector": text_vec,
                "clip_text_vector": clip_text_vec,
                "clip_image_vector": img_vec,
            }
        )
    return rows


def ingest_markdown(
    session: Session,
    file_row: FileRow,
    markdown_path: Path,
    client=None,
) -> int:
    """读 markdown -> 切块 -> 三路编码 -> 入库；返回 chunk 数并置 done。"""
    text = markdown_path.read_text(encoding="utf-8", errors="replace")
    chunks = chunk_markdown(text)
    if not chunks:
        raise RuntimeError(f"解析产物为空/全被过滤: {markdown_path}")
    rows = build_rows(file_row.id, file_row.filename, chunks, markdown_path)
    zilliz_store.upsert_chunks(rows, client=client)
    file_row.status = STATUS_DONE
    file_row.error = ""
    file_row.chunk_count = len(rows)
    session.commit()
    return len(rows)


def processed_dir_for(filename: str) -> Path:
    """该文档的 mineru 输出目录：data/processed/<原名去扩展名>/。"""
    stem = Path(filename).stem
    return PROCESSED_DIR / stem
