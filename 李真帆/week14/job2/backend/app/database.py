import sqlite3
from contextlib import contextmanager

from app.config import settings

DDL = """
CREATE TABLE IF NOT EXISTS documents (
  id          TEXT PRIMARY KEY,
  file_name   TEXT NOT NULL,
  file_size   INTEGER NOT NULL,
  file_path   TEXT NOT NULL,
  description TEXT DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'pending',
  error_msg   TEXT DEFAULT '',
  page_count  INTEGER DEFAULT 0,
  chunk_count INTEGER DEFAULT 0,
  updated_at  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id      TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_type  TEXT NOT NULL,
  heading     TEXT DEFAULT '',
  content     TEXT NOT NULL,
  image_path  TEXT DEFAULT '',
  embed_text  TEXT NOT NULL,
  embedding   BLOB,
  created_at  TEXT NOT NULL,
  UNIQUE(doc_id, chunk_index)
);

CREATE TABLE IF NOT EXISTS chat_sessions (
  id         TEXT PRIMARY KEY,
  title      TEXT NOT NULL DEFAULT '新会话',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id            TEXT PRIMARY KEY,
  session_id    TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role          TEXT NOT NULL,
  content       TEXT NOT NULL,
  image_path    TEXT DEFAULT '',
  meta          TEXT DEFAULT '',
  created_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON chat_messages(session_id);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(settings.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    settings.ensure_dirs()
    with get_conn() as conn:
        conn.executescript(DDL)
