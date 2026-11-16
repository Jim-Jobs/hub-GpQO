import json

from fastapi import APIRouter, HTTPException

from app.database import get_conn
from app.schemas import MessageOut, SessionOut
from app.utils import gen_id, now_iso

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionOut])
def list_sessions():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM chat_sessions ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    return [SessionOut(**dict(r)) for r in rows]


@router.post("", response_model=SessionOut)
def create_session():
    sid = gen_id()
    ts = now_iso()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?,?,?,?)",
            (sid, "新会话", ts, ts),
        )
        row = conn.execute("SELECT * FROM chat_sessions WHERE id=?", (sid,)).fetchone()
    return SessionOut(**dict(row))


@router.delete("/{session_id}")
def delete_session(session_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "会话不存在")
        conn.execute("DELETE FROM chat_messages WHERE session_id=?", (session_id,))
        conn.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
    return {"ok": True}


@router.get("/{session_id}/messages", response_model=list[MessageOut])
def list_messages(session_id: str):
    with get_conn() as conn:
        if conn.execute("SELECT 1 FROM chat_sessions WHERE id=?", (session_id,)).fetchone() is None:
            raise HTTPException(404, "会话不存在")
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id=? ORDER BY created_at, id",
            (session_id,),
        ).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        meta = None
        if d.get("meta"):
            try:
                meta = json.loads(d["meta"])
            except json.JSONDecodeError:
                meta = None
        items.append(MessageOut(**{**d, "meta": meta}))
    return items
