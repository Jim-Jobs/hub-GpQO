from __future__ import annotations


def test_data_paths_are_derived_from_data_dir(isolated_data_dir):
    from app.config import get_settings

    settings = get_settings()
    assert settings.logs_dir == settings.data_dir / "logs"
    assert settings.db_path.parent == settings.data_dir / "db"


def test_init_db_creates_tables_and_wal(isolated_data_dir):
    from sqlalchemy import inspect, text

    from app.db import engine, init_db, utcnow

    init_db()

    tables = set(inspect(engine).get_table_names())
    assert {"files", "kb_api_keys"} <= tables

    with engine.connect() as conn:
        journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
    assert str(journal_mode).lower() == "wal"

    assert utcnow().tzinfo is None
