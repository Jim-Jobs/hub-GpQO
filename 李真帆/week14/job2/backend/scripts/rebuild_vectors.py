"""切换 embedding 模型后重建全部向量。用法: python scripts/rebuild_vectors.py"""
from app.database import get_conn
from app.services import embed_service
from app.utils import now_iso


def main() -> None:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, embed_text FROM chunks ORDER BY doc_id, chunk_index"
        ).fetchall()
        total = len(rows)
        print(f"共 {total} 个切片，开始重建…")
        for i in range(0, total, 32):
            batch = rows[i : i + 32]
            vecs = embed_service.embed_texts([r["embed_text"] for r in batch])
            for r, v in zip(batch, vecs):
                conn.execute("UPDATE chunks SET embedding=? WHERE id=?", (embed_service.vec_to_blob(v), r["id"]))
            print(f"已重建 {min(i + 32, total)}/{total}")
        conn.execute("UPDATE documents SET updated_at=? WHERE status='completed'", (now_iso(),))
    print("完成")


if __name__ == "__main__":
    main()
