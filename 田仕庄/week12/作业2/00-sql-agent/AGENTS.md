# AGENTS.md

## Repo state

- Implementation lives in `src/sql_agent/`; tests in `tests/`; design in `docs/agent-design.md`. `ref/` contains the fixture DB and reference notebook/demo only. `README.md` is the human-facing entry point (pipeline overview, quick start, config table, known pitfalls) — keep it in sync when behavior or commands change.
- The pipeline is implemented: **DB parsing → table selection → SQL generation → SQL validation → SQL rewrite/repair → execution → result summarization**, built with **LangChain** (`langchain-core` + `langchain-openai`). The four LLM units live in `src/sql_agent/chains/` (formerly `agents/`); orchestration is still explicit code in `workflow.py`, not an autonomous agent.
- Agent boundaries, data contracts, retry policy, and code layout are specified in `docs/agent-design.md`; read it before changing the workflow.
- `ref/sql-agent.ipynb` is the behavioral reference but is **incomplete**: `SQLGeneratorAgent` and `SQLResultSummaryAgent` are stubs.
- `ref/agent-demo.py` is the style reference for the **legacy** openai-agents implementation (module-level `Agent`, Pydantic `output_type`, explicit `Workflow` class, final JSON assembled by code). The runtime has been migrated to LangChain; `ref/` was intentionally **not** migrated and should be read as historical reference only.
- Do not edit `ref/` as part of normal implementation work; treat it as read-only fixture/spec. Put new code outside `ref/`.

## Environment / commands

- Interpreter: conda env `py312` at `C:/Users/Cooper/miniconda3/envs/py312/python.exe` (Python 3.12.13). Use it for every command below; do not rely on other `python` on PATH.
- Installed and verified in `py312`: `pytest` 9.0.3, `ruff` 0.17.3, `pydantic` 2.13.5, `sqlalchemy` 2.0.51, `openai` 2.45.0, `langchain` 1.3.18, `langchain-core` 1.6.1, `langchain-openai` 1.1.9, `langgraph` 1.2.11, `python-dotenv` 1.2.2. `openai-agents` 0.20.0 is still installed but **no longer used** by the runtime. This project itself is installed **editable** (`pip install -e . --no-deps`, `chinook-sql-agent` 0.1.0), so `src/` is importable from any cwd. `--no-deps` matters: without it pip re-resolves `pyproject.toml` and would pull a different `sqlalchemy`/`openai-agents`.
- Runtime dependencies are `langchain-core` + `langchain-openai` + `sqlalchemy` + `python-dotenv` (see `pyproject.toml`). Do not import the top-level `langchain` package or `langgraph` from `src/` — the workflow is hand-rolled and keeping the dependency surface small is deliberate.
- Run the CLI: `python -m sql_agent "总共有多少张专辑？"` (the editable install already exposes `src/`; `PYTHONPATH=src python3 -m sql_agent` remains a valid fallback). The `sql-agent` console script lives at `C:/Users/Cooper/miniconda3/envs/py312/Scripts/sql-agent.exe`, which is **not on PATH** — call it by full path or use the module form. Requires `OPENAI_API_KEY` (or `DEEPSEEK_API_KEY`) from the environment or a local `.env`; `OPENAI_BASE_URL` defaults to the DeepSeek endpoint `https://api.deepseek.com`.
- Model: `deepseek-flash` (= DeepSeek-V4.1-Flash-1017) is the correct current model id; `deepseek-v4-flash` is a legacy alias for a retired model and `deepseek-chat` / `deepseek-reasoner` were deprecated 2026-07-24. Thinking mode is **on by default** with `reasoning_effort=high`, and `temperature` has **no effect** in thinking mode — do not rely on `temperature=0` for determinism.
- Run tests: `"$PY" -m pytest -q` (no API key needed; the four chain factories are monkeypatched, so nothing hits the network; baseline **37 passed**). Tests locate `ref/chinook.db` through `sql_agent.config.PROJECT_ROOT`, never through `Path.cwd()` — so any working directory works, but running from **outside** the repo root requires the editable install, otherwise `import sql_agent` fails. Lint: `"$PY" -m ruff check src tests` (currently clean).
- Logs go to stderr; the final JSON goes to stdout. Control with `--log-level DEBUG|INFO|WARNING|ERROR` or `SQL_AGENT_LOG_LEVEL`.
- Model overrides: `SQL_AGENT_MODEL` and `SQL_AGENT_SUMMARY_MODEL` (both default `deepseek-flash`). Other knobs: `SQL_AGENT_THINKING` (`auto`|`enabled`|`disabled`), `SQL_AGENT_LLM_TIMEOUT`, `SQL_AGENT_LLM_MAX_RETRIES`.
- LangSmith tracing is opt-in via `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY`. `tests/conftest.py` force-disables it so test runs never upload. Traces include full prompts and up to 50 result rows — do not enable against real business data without redaction.
- Run the notebook: `jupyter lab ref/sql-agent.ipynb`.
- Important: the notebook hardcodes `DBParser('sqlite:///./chinook.db')`, so it only resolves when the kernel cwd is `ref/`. From the repo root the correct URL is `sqlite:///ref/chinook.db`. The docstring form `sqlite://chinook.db` is invalid for a relative path.
- Smoke-test the DB: `python -c "import sqlite3; print(sqlite3.connect('ref/chinook.db').execute('select count(*) from tracks').fetchone())"` → `(3503,)`.

## Database facts

- SQLAlchemy `inspect(engine).get_table_names()` returns 11 business tables. Raw `sqlite_master` also contains `sqlite_sequence` and `sqlite_stat1`; filter `sqlite_%` whenever querying raw SQLite metadata, or table selection may pick them.
- Tables: `albums` (347), `artists` (275), `customers` (59), `employees` (8), `genres` (25), `invoice_items` (2240), `invoices` (412), `media_types` (5), `playlist_track` (8715), `playlists` (18), `tracks` (3503).
- FK graph: `albums.ArtistId→artists`; `tracks.AlbumId→albums`, `tracks.GenreId→genres`, `tracks.MediaTypeId→media_types`; `invoice_items.InvoiceId→invoices`, `invoice_items.TrackId→tracks`; `invoices.CustomerId→customers`; `customers.SupportRepId→employees`; `employees.ReportsTo→employees` (self); `playlist_track.PlaylistId→playlists`, `playlist_track.TrackId→tracks`.
- Column names are PascalCase (`ArtistId`, `InvoiceDate`, `UnitPrice`); table names are lowercase. Use the exact casing in generated SQL and prompts.

## Reference implementation gotchas

- `DBParser.__init__` eagerly profiles **every column of every table** (distinct count, mode, NaN count, min/max, sample values) and prints each table. It takes ~0.8s on Chinook; instantiate once and reuse/cache, never per request.
- `DBParser.check_sql()` is not a dry run: it executes the SQL through `engine.begin()` (auto-commit) and returns `(ok, traceback)`. Never pass generated `INSERT/UPDATE/DELETE/DDL` to it. Validate on a read-only connection or with `EXPLAIN`.
- `DBParser.execute_sql()` returns `list[Row]` but is annotated `-> bool`. Fix the annotation in new code.
- `DBParser.get_table_sample()` returns exactly **one** row, not a representative sample.
- `get_table_fields(table).to_markdown()` depends on `tabulate` and produces the schema text sent to prompts.
- The synthetic Q/A generator (cells 16–18) is debug-limited: it uses `parser.table_names[:1]` and `break`s after the first table, retries 5×, and keeps only SQL results with exactly one row. Do not treat it as a full eval harness. `gt_qes_answer` rows are `[rewritten question, table, sql, raw result, NL answer]`.
- Cell 13 hardcodes a live DashScope API key and base URL. Never copy/reuse/commit that key; read `OPENAI_API_KEY` / `OPENAI_BASE_URL` from the environment and keep secrets out of the notebook.
- Model usage in the reference: `Agent(model="qwen-max")` for planning, `qwen-plus` for the plain `openai` helper, through the DashScope OpenAI-compatible endpoint (`https://dashscope.aliyuncs.com/compatible-mode/v1`). `Runner.run` is async. For an explicit client, pair `AsyncOpenAI` with `OpenAIChatCompletionsModel`.
- Cells 17 drive an LLM per field combination; do not execute them wholesale during routine checks (quota/time).
- Chinese is used for comments, docstrings, and prompts. Keep user-facing prompts Chinese when extending the workflow.

## Implementation constraints

- All DB access is read-only. SQLAlchemy uses `sqlite:///file:<url-encoded-abs-path>?mode=ro&uri=true`; raw `sqlite3` uses `Path.as_uri()?mode=ro`. Do not switch to a writable connection.
- `SQLValidator` must only compile with `EXPLAIN`, never execute. Safety checks run before compilation: single statement, SELECT/WITH prefix, forbidden keywords outside strings/comments. **`FORBIDDEN_KEYWORDS` must NOT include `replace`**: `REPLACE(x, a, b)` is a legitimate string function and banning it forces the rewriter to abandon the user's intended transformation (silent wrong answers); write-form `REPLACE INTO` is already blocked by the SELECT/WITH prefix check.
- `SQLExecutor.row_count` is always the **real total row count**: when the result is truncated, a one-shot `SELECT COUNT(*) FROM (<sql>)` re-query fills it in (works for `WITH` / `WITH RECURSIVE` too); `rows` holds the capped rows. Trailing semicolons before an end-of-line comment are not multi-statement violations.
- Structured-output parse failures (no tool call, schema-validation error, `OutputParserException`) are retried **once** via `workflow._ainvoke_structured`; the raw error only goes to logs — user-facing error text stays clean. Do not re-raise pydantic errors into `answer`. Required fields on `TableSelection` / `SQLDraft` / `FinalAnswer` therefore have defaults and degrade into repair/clarify/fallback paths instead of crashing the run.
- Numeric env knobs are clamped in `config.py` (`max(..., minimum)`); an empty/whitespace `SQL_AGENT_DB` falls back to the default DB. Do not remove the clamps — `MAX_REPAIR_ATTEMPTS=-1` otherwise produces an unrecoverable `unknown error`.
- Structured output goes through `llm.structured()` in `src/sql_agent/llm.py`: `bind_tools([Schema], tool_choice="auto")` → `PydanticToolsParser` → `_unwrap`. **Do not switch to `llm.with_structured_output(..., method="function_calling")`** — it pins `tool_choice` to a named function and DeepSeek's thinking mode (on by default) rejects it with `400 Thinking mode does not support this tool_choice`.
- `chains/__init__.py` must stay side-effect free: chain factories are `@lru_cache` and lazy, because `ChatOpenAI(...)` validates the API key at construction time. Module-level instantiation would break every offline test at import.
- `models.ErrorCode` defines `__str__ = str.__str__` on purpose: Python 3.11+ would otherwise format it as `ErrorCode.SYNTAX` in prompts and logs.
- Tests must build absolute paths from `sql_agent.config.PROJECT_ROOT` (see `CHINOOK_DB` in `tests/test_db_tools.py`). A bare `Path("ref/chinook.db")` resolves against cwd and silently fails with `sqlite3.OperationalError: unable to open database file` whenever pytest is not launched from the repo root.
- `ResultSummaryAgent` is skipped when the result is empty; the workflow returns a deterministic "未查询到符合条件的数据。" to avoid speculation.
- Keep comments, docstrings, prompts, and user-facing answers in Chinese to match the reference.
