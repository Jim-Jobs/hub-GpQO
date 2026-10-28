"""配置夹逼回归：数值环境变量越界 / 非法 / 空串时的行为。"""

from sql_agent.config import PROJECT_ROOT, Settings


def test_settings_clamps_invalid_numeric_env(monkeypatch) -> None:
    monkeypatch.setenv("SQL_AGENT_MAX_REPAIR_ATTEMPTS", "-1")
    monkeypatch.setenv("SQL_AGENT_MAX_ROWS", "0")
    monkeypatch.setenv("SQL_AGENT_MAX_SUMMARY_ROWS", "0")
    monkeypatch.setenv("SQL_AGENT_MAX_CELL_CHARS", "0")
    monkeypatch.setenv("SQL_AGENT_QUERY_TIMEOUT", "-5")
    monkeypatch.setenv("SQL_AGENT_LLM_TIMEOUT", "0")

    s = Settings.from_env()

    assert s.max_repair_attempts == 0  # 0 合法：只校验不修复
    assert s.max_result_rows == 1
    assert s.max_summary_rows == 1
    assert s.max_cell_chars == 1
    assert s.query_timeout_seconds == 0.1
    assert s.llm_timeout_seconds == 1.0


def test_settings_nonnumeric_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("SQL_AGENT_MAX_ROWS", "abc")
    monkeypatch.setenv("SQL_AGENT_QUERY_TIMEOUT", "not-a-number")

    s = Settings.from_env()

    assert s.max_result_rows == 200
    assert s.query_timeout_seconds == 5.0


def test_settings_empty_db_env_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("SQL_AGENT_DB", "   ")

    s = Settings.from_env()

    assert s.db_path == PROJECT_ROOT / "ref" / "chinook.db"


def test_settings_relative_db_env_resolves_against_project_root(monkeypatch) -> None:
    monkeypatch.setenv("SQL_AGENT_DB", "ref/chinook.db")

    assert Settings.from_env().db_path == PROJECT_ROOT / "ref" / "chinook.db"
