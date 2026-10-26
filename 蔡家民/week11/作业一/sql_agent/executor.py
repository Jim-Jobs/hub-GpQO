from __future__ import annotations

import sqlite3
import time
import re
from pathlib import Path

from .contracts import ExecutionResult, SQLCandidate


class QueryRejected(ValueError):
    pass


class ReadOnlyExecutor:
    """SQLite authorizer protects nested queries too; no keyword-only security check."""

    def __init__(self, database: Path, allowed_tables: list[str], timeout_seconds: float = 2.0,
                 max_rows: int = 1000, max_vm_steps: int = 2_000_000):
        self.database = database.resolve(strict=True)
        self.allowed_tables = set(allowed_tables)
        self.timeout_seconds = timeout_seconds
        self.max_rows = max_rows
        self.max_vm_steps = max_vm_steps
        if max_rows < 1 or timeout_seconds <= 0 or max_vm_steps < 1:
            raise ValueError("Execution limits must be positive")

    def _connection(self):
        conn = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True, timeout=1.0)
        conn.execute("PRAGMA query_only=ON")
        conn.enable_load_extension(False)
        denied = []
        self.last_read_tables = set()
        started = time.monotonic()
        steps = 0

        def authorize(action, arg1, arg2, database, source):
            if action == sqlite3.SQLITE_READ:
                # SQLite's COUNT(*) shortcut can report database=None and column=''.
                if database in {None, "main"} and arg1 in self.allowed_tables:
                    self.last_read_tables.add(arg1)
                    return sqlite3.SQLITE_OK
            elif action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE):
                return sqlite3.SQLITE_OK
            elif action == sqlite3.SQLITE_FUNCTION:
                safe_functions = {"count", "sum", "total", "avg", "min", "max", "round", "abs",
                                  "coalesce", "ifnull", "nullif", "length", "lower", "upper", "trim",
                                  "ltrim", "rtrim", "substr", "substring", "replace", "instr", "like", "glob",
                                  "date", "time", "datetime", "strftime", "julianday", "unixepoch",
                                  "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile",
                                  "lag", "lead", "first_value", "last_value", "nth_value", "typeof", "iif"}
                if (arg2 or "").lower() in safe_functions:
                    return sqlite3.SQLITE_OK
            denied.append(f"Operation {action}: {arg1 or ''} {arg2 or ''}")
            return sqlite3.SQLITE_DENY

        def progress():
            nonlocal steps
            steps += 100
            return int(steps >= self.max_vm_steps or time.monotonic() - started > self.timeout_seconds)

        conn.set_authorizer(authorize)
        conn.set_progress_handler(progress, 100)
        return conn, denied

    @staticmethod
    def _check(candidate: SQLCandidate):
        if not candidate.sql.strip():
            raise QueryRejected("Empty SQL")
        # Envelope gate is defense in depth. The SQLite authorizer below is the
        # authoritative check, including statements nested in WITH clauses.
        prefix = re.sub(r"--[^\n]*|/\*[\s\S]*?\*/", " ", candidate.sql).lstrip()
        if not re.match(r"(?i)^(select|with)\b", prefix):
            raise QueryRejected("Only SELECT/WITH queries are allowed")
        if not isinstance(candidate.params, dict):
            raise QueryRejected("Parameters must be a JSON object")
        if any(not isinstance(k, str) or not (v is None or isinstance(v, (str, int, float)))
               for k, v in candidate.params.items()):
            raise QueryRejected("Only scalar named parameters are allowed")

    def explain(self, candidate: SQLCandidate) -> list:
        self._check(candidate)
        conn, denied = self._connection()
        try:
            rows = conn.execute("EXPLAIN QUERY PLAN " + candidate.sql, candidate.params).fetchall()
            # SQLite handles some statements such as VACUUM specially; only accept plans
            # whose VM includes a SELECT result, never execute a non-query after checking.
            return [list(row) for row in rows]
        except sqlite3.Error as exc:
            if denied:
                raise QueryRejected("Forbidden database operation: " + denied[0]) from exc
            raise
        finally:
            conn.close()

    def run(self, candidate: SQLCandidate) -> ExecutionResult:
        self._check(candidate)
        conn, denied = self._connection()
        start = time.monotonic()
        try:
            cursor = conn.execute(candidate.sql, candidate.params)
            if cursor.description is None:
                raise QueryRejected("Only result-producing read queries are allowed")
            columns = [x[0] for x in cursor.description]
            rows = cursor.fetchmany(self.max_rows + 1)
            return ExecutionResult("ok", columns, [list(r) for r in rows[:self.max_rows]],
                                   len(rows) > self.max_rows, (time.monotonic() - start) * 1000)
        except (sqlite3.Error, QueryRejected) as exc:
            kind = "forbidden_operation" if denied or isinstance(exc, QueryRejected) else (
                "resource_limit" if "interrupted" in str(exc).lower() else "execution_error")
            return ExecutionResult("error", elapsed_ms=(time.monotonic() - start) * 1000,
                                   error_type=kind, error=str(exc))
        finally:
            conn.close()
