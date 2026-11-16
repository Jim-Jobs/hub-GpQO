import base64
import json
import logging

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from app.config import settings
from app.database import get_conn
from app.services import llm_service, rag_service
from app.utils import gen_id, now_iso

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

MAX_IMAGE_SIZE = 10 * 1024 * 1024
ALLOWED_IMAGE_EXTS = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp"}
HISTORY_LIMIT = 10

RAG_SYSTEM_PROMPT = (
    "你是知识库问答助手。请优先依据下面的知识库片段回答用户问题，表述与片段保持一致。\n"
    "片段中若包含 markdown 图片链接（![](/static/...)），在回答的合适位置原样输出该图片链接，"
    "让前端展示图片；禁止编造、拼接或修改任何图片链接。\n"
    "片段中未涵盖的内容，可如实说明“知识库中未提及”。不要提及“片段”字样，直接给出答案。\n\n"
    "【知识库片段】\n{context}"
)

FREE_SYSTEM_PROMPT = (
    "你是知识库问答助手。当前问题与知识库内容不相关，请基于你自己的知识自由回答，保持准确简洁。"
    "禁止输出任何图片链接。"
)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("")
def chat(
    session_id: str = Form(...),
    content: str = Form(...),
    image: UploadFile | None = File(None),
):
    content = (content or "").strip()
    if not content:
        raise HTTPException(400, "提问内容不能为空")

    image_bytes: bytes | None = None
    image_ext: str | None = None
    if image is not None and image.filename:
        suffix = "." + image.filename.rsplit(".", 1)[-1].lower() if "." in image.filename else ""
        if suffix not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(400, f"仅支持图片格式: {'/'.join(ALLOWED_IMAGE_EXTS)}")
        image_bytes = image.file.read()
        if len(image_bytes) > MAX_IMAGE_SIZE:
            raise HTTPException(413, "图片超过 10MB")
        image_ext = suffix

    settings.ensure_dirs()
    user_msg_id = gen_id()
    image_path = ""
    if image_bytes is not None:
        image_path = f"chat_images/{user_msg_id}{image_ext}"
        (settings.data_dir / image_path).write_bytes(image_bytes)

    ts = now_iso()
    with get_conn() as conn:
        session = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if session is None:
            raise HTTPException(404, "会话不存在")
        conn.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, image_path, meta, created_at)"
            " VALUES (?,?,?,?,?,'',?)",
            (user_msg_id, session_id, "user", content, image_path, ts),
        )
        title = session["title"]
        if title == "新会话":
            title = content[:20]
        conn.execute(
            "UPDATE chat_sessions SET title=?, updated_at=? WHERE id=?", (title, ts, session_id)
        )
        history_rows = conn.execute(
            "SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY created_at DESC, id DESC LIMIT ?",
            (session_id, HISTORY_LIMIT),
        ).fetchall()
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(history_rows)][:-1]

    data_uri = None
    if image_bytes is not None:
        mime = ALLOWED_IMAGE_EXTS[image_ext]
        data_uri = f"data:image/{mime};base64," + base64.b64encode(image_bytes).decode()

    def gen():
        assistant_id = gen_id()
        try:
            refs = rag_service.retrieve(content)
            related, reason = False, ""
            if refs:
                related, reason = llm_service.judge_relevance(
                    content,
                    [{"doc_name": r["doc_name"], "heading": r["heading"],
                      "preview": r["content"][:200]} for r in refs],
                )

            if related:
                system = RAG_SYSTEM_PROMPT.format(context=rag_service.build_context(refs))
            else:
                system = FREE_SYSTEM_PROMPT

            if data_uri is not None:
                user_content = llm_service.image_content(content, data_uri)
                model = settings.vision_model
            else:
                user_content = content
                model = settings.chat_model

            messages = (
                [{"role": "system", "content": system}]
                + history
                + [{"role": "user", "content": user_content}]
            )

            parts: list[str] = []
            for delta in llm_service.stream_answer(messages, model):
                parts.append(delta)
                yield _sse("token", {"text": delta})

            answer = "".join(parts)
            meta = {
                "related": related,
                "reason": reason,
                "model": model,
                "refs": [
                    {"doc_name": r["doc_name"], "heading": r["heading"],
                     "chunk_type": r["chunk_type"], "score": r["score"]}
                    for r in refs
                ],
            }
            with get_conn() as conn:
                conn.execute(
                    "INSERT INTO chat_messages (id, session_id, role, content, image_path, meta, created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (assistant_id, session_id, "assistant", answer, "",
                     json.dumps(meta, ensure_ascii=False), now_iso()),
                )
                conn.execute(
                    "UPDATE chat_sessions SET updated_at=? WHERE id=?", (now_iso(), session_id)
                )
            yield _sse("done", {"message_id": assistant_id, "meta": meta})
        except Exception as e:
            logger.exception("chat 生成失败, session=%s", session_id)
            yield _sse("error", {"message": f"{type(e).__name__}: {e}"[:200]})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
