"""Offline stage checks, replayed repair, and discriminating synthetic data."""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from .catalog import parse_database
from .contracts import QueryIntent, SQLCandidate
from .executor import ReadOnlyExecutor
from .model import ReplayModel
from .validator import validate
from .workflow import Workflow


def validation_cases():
    cases = []

    def add(group, sql, fixed, tables, intent=None, params=None):
        cases.append({"id": f"V{len(cases)+1:02}", "group": group, "sql": sql,
                      "fixed_sql": fixed, "tables": tables, "intent": intent or {}, "params": params or {}})

    count = "SELECT COUNT(*) AS customers FROM customers"
    for sql in ["SELECT MissingColumn FROM customers", "SELECT * FROM unknown_table",
                "SELECT FROM customers", "SELECT COUNT(*) FROM customers WHERE CustomerId=:missing",
                "SELECT SUM(COUNT(*)) FROM customers", "SELECT Country FROM tracks",
                "SELECT CustomerId FROM customers UNION SELECT InvoiceId, Total FROM invoices",
                "SELECT c.CustomerId FROM customers c JOIN invoices i ON"]:
        add("compile_error", sql, count, ["customers"])
    final_amount = "SELECT SUM(ii.UnitPrice*ii.Quantity) AS revenue FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId"
    for sql in ["SELECT SUM(i.Total) FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId",
                "SELECT SUM(Total) FROM invoices JOIN invoice_items ON invoices.InvoiceId=invoice_items.InvoiceId",
                "SELECT SUM(i.Total) FROM invoices i LEFT JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId",
                'SELECT SUM(i."Total") FROM invoices i JOIN "invoice_items" ii ON ii.InvoiceId=i.InvoiceId']:
        add("aggregation_grain", sql, final_amount, ["invoices", "invoice_items"])
    final_date = "SELECT SUM(Total) FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end"
    intent = {"time_range": ["2013-01-01", "2014-01-01"]}
    params = {"start": "2013-01-01", "end": "2014-01-01"}
    for sql in ["SELECT SUM(Total) FROM invoices",
                "SELECT SUM(Total) FROM invoices WHERE InvoiceDate>='2013-01-01'",
                "SELECT SUM(Total) FROM invoices WHERE InvoiceDate<'2014-01-01'",
                "SELECT SUM(Total) FROM invoices WHERE InvoiceDate>='2012-01-01' AND InvoiceDate<'2013-01-01'"]:
        add("missing_time_filter", sql, final_date, ["invoices"], intent, params)
    for sql in ["DELETE FROM customers", "UPDATE customers SET FirstName='x'", "DROP TABLE tracks",
                "PRAGMA writable_schema=ON", "ATTACH DATABASE ':memory:' AS other",
                "WITH x AS (SELECT 1) DELETE FROM customers", "SELECT load_extension('x')",
                "SELECT sql FROM sqlite_master"]:
        add("forbidden_operation", sql, None, ["customers"])
    for sql, tables, intent_value, values in [
        (count, ["customers"], {}, {}),
        ("SELECT SUM(Total) FROM invoices", ["invoices"], {}, {}),
        (final_amount, ["invoices", "invoice_items"], {}, {}),
        ("WITH totals AS (SELECT InvoiceId, SUM(UnitPrice*Quantity) AS revenue FROM invoice_items GROUP BY InvoiceId) SELECT SUM(revenue) FROM totals", ["invoice_items"], {}, {}),
        (final_date, ["invoices"], intent, params),
        ("SELECT InvoiceId FROM invoices WHERE InvoiceId=-1", ["invoices"], {}, {}),
    ]:
        add("valid", sql, sql, tables, intent_value, values)
    assert len(cases) == 30
    return cases


def run_validation_benchmark(root: Path) -> dict:
    catalog = parse_database(root / "data/chinook.db")
    executor = ReadOnlyExecutor(root / "data/chinook.db", list(catalog["tables"]))
    records = []
    for case in validation_cases():
        report = validate(SQLCandidate(case["sql"], case["params"]), QueryIntent(**case["intent"]), executor)
        detected = {c.error_type for c in report.checks if not c.passed}
        correct = report.passed if case["group"] == "valid" else case["group"] in detected
        record = {"id": case["id"], "expected": case["group"], "passed": report.passed,
                  "detected": sorted(detected), "classification_correct": correct}
        if case["fixed_sql"] and case["group"] != "valid":
            fixture = {"intent": {"status": "answerable", **case["intent"]},
                       "table_selection": {"tables": case["tables"]}, "semantic_review": {"issues": []},
                       "candidates": [{"sql": case["sql"], "params": case["params"]},
                                      {"sql": case["fixed_sql"], "params": case["params"], "changes": "Injected-error repair fixture"}]}
            state = Workflow(root / "data/chinook.db", ReplayModel(fixture), root / "config/business_rules.json",
                             root / "artifacts/diagnostics").run("专项回放：修复已注入的SQL错误", "2014-01-15")
            record.update(repair_status=state.status, repair_candidates=len(state.candidates), run_id=state.run_id)
        records.append(record)
    invalid = [r for r in records if r["expected"] != "valid"]
    valid = [r for r in records if r["expected"] == "valid"]
    repairable = [r for r in records if "repair_status" in r]
    result = {"mode": "deterministic validation + injected replay repair; NOT model scores", "total": len(records),
              "classification_correct": sum(r["classification_correct"] for r in records),
              "error_detection": {"detected": sum(not r["passed"] for r in invalid), "total": len(invalid)},
              "false_positives": {"count": sum(not r["passed"] for r in valid), "total": len(valid)},
              "scripted_repair": {"successful": sum(r["repair_status"] == "success" for r in repairable), "total": len(repairable)},
              "records": records}
    destination = root / "reports/validation_benchmark.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_counterexamples(root: Path) -> dict:
    """Use separate synthetic DB; course DB remains unchanged."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "counterexamples.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE invoices(InvoiceId INTEGER PRIMARY KEY, Total REAL);
            CREATE TABLE invoice_items(InvoiceId INTEGER, UnitPrice REAL, Quantity INTEGER);
            INSERT INTO invoices VALUES(1,10),(2,20);
            INSERT INTO invoice_items VALUES(1,4,1),(1,6,1),(2,10,2);
        """)
        conn.commit()
        conn.close()
        executor = ReadOnlyExecutor(path, ["invoices", "invoice_items"])
        wrong = executor.run(SQLCandidate("SELECT SUM(i.Total) FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId"))
        header = executor.run(SQLCandidate("SELECT SUM(Total) FROM invoices"))
        detail = executor.run(SQLCandidate("SELECT SUM(UnitPrice*Quantity) FROM invoice_items"))
        lines = executor.run(SQLCandidate("SELECT COUNT(*), SUM(Quantity), COUNT(DISTINCT InvoiceId) FROM invoice_items"))
        empty = executor.run(SQLCandidate("SELECT InvoiceId FROM invoices WHERE InvoiceId=99"))
        null = executor.run(SQLCandidate("SELECT SUM(Total) FROM invoices WHERE InvoiceId=99"))
        zero = executor.run(SQLCandidate("SELECT COUNT(*) FROM invoices WHERE InvoiceId=99"))
    records = [
        {"name": "header_join_double_count", "passed": wrong.rows == [[40.0]] and header.rows == detail.rows == [[30.0]],
         "wrong": wrong.rows, "correct_header": header.rows, "correct_detail": detail.rows},
        {"name": "lines_units_orders_differ", "passed": lines.rows == [[3, 4, 2]], "values": lines.rows},
        {"name": "empty_null_zero_differ", "passed": empty.rows == [] and null.rows == [[None]] and zero.rows == [[0]],
         "empty": empty.rows, "null": null.rows, "zero": zero.rows},
    ]
    result = {"total": len(records), "passed": sum(r["passed"] for r in records), "records": records}
    (root / "reports/counterexamples.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
