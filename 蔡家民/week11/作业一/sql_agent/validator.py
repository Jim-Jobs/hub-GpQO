from __future__ import annotations

import re
import sqlite3

from .contracts import Check, QueryIntent, SQLCandidate, ValidationReport
from .executor import QueryRejected, ReadOnlyExecutor


def stripped_sql(sql: str) -> str:
    """Lex literals/comments for diagnostics only, NOT the security boundary."""
    return re.sub(r"--[^\n]*|/\*[\s\S]*?\*/|'(?:''|[^'])*'", " ", sql).lower()


def validate(candidate: SQLCandidate, intent: QueryIntent, executor: ReadOnlyExecutor,
             selected_tables: list[str] | None = None) -> ValidationReport:
    checks = []
    sql = stripped_sql(candidate.sql).strip()
    if not re.match(r"^(select|with)\b", sql):
        return ValidationReport(False, [Check("query_envelope", False, "forbidden_operation",
                                             "Only SELECT/WITH read queries are accepted", False)])
    try:
        executor.explain(candidate)
        checks.append(Check("sqlite_compile_and_authorize", True))
    except QueryRejected as exc:
        checks.append(Check("sqlite_compile_and_authorize", False, "forbidden_operation", str(exc), False))
    except (sqlite3.Error, ValueError, TypeError) as exc:
        checks.append(Check("sqlite_compile_and_authorize", False, "compile_error", str(exc)))
    if not all(c.passed for c in checks):
        return ValidationReport(False, checks)
    if selected_tables is not None:
        outside = executor.last_read_tables - set(selected_tables)
        checks.append(Check("selection_scope", not outside, "selection_mismatch" if outside else "",
                            "SQL references unselected tables: " + ", ".join(sorted(outside)) if outside else ""))

    # Narrow high-risk heuristic. Exempt aggregate subqueries/CTEs because regex
    # cannot prove their grain; live semantic review and result-based evaluation remain needed.
    joined_detail = bool(re.search(r"\bjoin\s+[\"`\[]?invoice_items\b", sql))
    sum_header = bool(re.search(r"\bsum\s*\(\s*(?:\w+\s*\.\s*)?[\"`\[]?total[\"`\]]?\s*\)", sql))
    nested = len(re.findall(r"\bselect\b", sql)) > 1
    risk = joined_detail and sum_header and not nested
    checks.append(Check("header_detail_grain_risk", not risk, "aggregation_grain" if risk else "",
                        "SUM(order Total) after joining invoice_items can duplicate order totals" if risk else ""))
    # Confirmed dates may appear as SQL literals or bound values. This check only
    # detects absence of confirmed boundaries; it is not a general predicate proof.
    if intent.time_range:
        used_parameters = set(re.findall(r":([A-Za-z_]\w*)", stripped_sql(candidate.sql)))
        evidence = candidate.sql + " " + " ".join(str(v) for k, v in candidate.params.items() if k in used_parameters)
        missing = [d for d in intent.time_range if d not in evidence]
        checks.append(Check("confirmed_time_boundaries", not missing, "missing_time_filter" if missing else "",
                            "Confirmed boundaries absent: " + ", ".join(missing) if missing else ""))
    return ValidationReport(all(c.passed for c in checks), checks)
