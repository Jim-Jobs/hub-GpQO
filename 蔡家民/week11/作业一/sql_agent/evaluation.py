from __future__ import annotations

import json
import math
from pathlib import Path

from .contracts import ExecutionResult, SQLCandidate
from .executor import ReadOnlyExecutor


def scalar_equal(left, right, abs_tol=1e-6, rel_tol=1e-6):
    if left is None or right is None:
        return left is right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(left, right, abs_tol=abs_tol, rel_tol=rel_tol)
    return type(left) is type(right) and left == right


def results_equal(actual: ExecutionResult, expected: ExecutionResult, ordered=False) -> bool:
    """Compare positional semantic columns. Aliases may differ; multiplicity may not."""
    if actual.status != "ok" or expected.status != "ok" or actual.truncated or expected.truncated:
        return False
    if len(actual.columns) != len(expected.columns) or len(actual.rows) != len(expected.rows):
        return False

    def row_equal(a, b):
        return len(a) == len(b) and all(scalar_equal(x, y) for x, y in zip(a, b))

    if ordered:
        return all(row_equal(a, b) for a, b in zip(actual.rows, expected.rows))
    # One-to-one matching with numeric tolerance; sufficient for this small corpus.
    # For huge result sets a typed keyed comparator should replace this O(n^2) routine.
    remaining = list(expected.rows)
    for row in actual.rows:
        for i, ref in enumerate(remaining):
            if row_equal(row, ref):
                remaining.pop(i)
                break
        else:
            return False
    return True


def validate_dataset(cases: list[dict], executor: ReadOnlyExecutor) -> list[dict]:
    errors = []
    ids = set()
    family_splits = {}
    for case in cases:
        if case["id"] in ids:
            errors.append({"case": case["id"], "error": "Duplicate ID"})
        ids.add(case["id"])
        family = case["template_family"]
        if family in family_splits and family_splits[family] != case["split"]:
            errors.append({"case": case["id"], "error": "Template family leaks across splits"})
        family_splits[family] = case["split"]
        if case["expected_status"] == "success":
            result = executor.run(SQLCandidate(case["reference_sql"], case.get("params", {})))
            if result.status != "ok" or result.truncated:
                errors.append({"case": case["id"], "error": result.error or "Reference truncated"})
            if case.get("reference_snapshot"):
                ref = case["reference_snapshot"]
                if not results_equal(result, ExecutionResult("ok", ref["columns"], ref["rows"]), case.get("ordered", False)):
                    errors.append({"case": case["id"], "error": "Saved reference results changed"})
    return errors


def evaluate_case(case: dict, workflow, judge=None) -> dict:
    state = workflow.run(case["question"], case["reference_date"], case.get("clarification", ""))
    expected_status = case["expected_status"]
    record = {"case_id": case["id"], "split": case["split"], "category": case["category"],
              "mode": state.mode, "run_id": state.run_id, "expected_status": expected_status,
              "actual_status": state.status, "status_correct": state.status == expected_status,
              "result_correct": None, "first_sql_executable": None,
              "necessary_table_recall": None, "extra_table_count": None,
              "rewrites": max(0, len(state.candidates) - 1), "error": state.error,
              "first_detected_failure": next((e["node"] for e in state.trace if e["status"] == "failed"), None)}
    if state.selection and case.get("required_tables"):
        expected = set(case["required_tables"])
        selected = set(state.selection.tables)
        record["necessary_table_recall"] = len(expected & selected) / len(expected)
        record["extra_table_count"] = len(selected - expected)
    if state.candidates:
        try:
            workflow.executor.explain(state.candidates[0])
            record["first_sql_executable"] = True
        except Exception:
            record["first_sql_executable"] = False
    if expected_status == "success":
        expected = workflow.executor.run(SQLCandidate(case["reference_sql"], case.get("params", {})))
        record["reference_execution_ok"] = expected.status == "ok" and not expected.truncated
        record["result_correct"] = bool(record["reference_execution_ok"] and state.status == "success" and
                                        state.execution and results_equal(state.execution, expected, case.get("ordered", False)))
        if judge and state.answer and record["reference_execution_ok"]:
            from .prompts import JUDGE
            try:
                verdict = judge.complete("judge", JUDGE, {"question": case["question"], "key_facts": case.get("key_facts", []),
                                         "reference": {"columns": expected.columns, "rows": expected.rows},
                                         "answer": state.answer.text})
                for key in ("faithfulness", "coverage", "limitations"):
                    if type(verdict.get(key)) is not int or not 1 <= verdict[key] <= 5:
                        raise ValueError(f"Invalid judge score: {key}")
                if type(verdict.get("uncertain")) is not bool:
                    raise ValueError("Judge must report uncertainty")
                record["judge"] = verdict
                record["judge_model"] = judge.model
            except Exception as exc:
                record["judge_error"] = str(exc)
    record["task_correct"] = record["result_correct"] if expected_status == "success" else record["status_correct"]
    metadata = state.trace[-1]["output"]
    record["elapsed_ms"] = metadata["elapsed_ms"]
    record["model_calls"] = len(metadata["calls"])
    record["usage"] = [c["usage"] for c in metadata["calls"] if c.get("usage")]
    return record


def aggregate(records: list[dict]) -> dict:
    answerable = [r for r in records if r["expected_status"] == "success"]
    nonanswerable = [r for r in records if r["expected_status"] != "success"]
    groups = {}
    for category in sorted({r["category"] for r in records}):
        group = [r for r in records if r["category"] == category]
        groups[category] = {"correct": sum(bool(r["task_correct"]) for r in group), "total": len(group)}
    mode = records[0]["mode"] if records else "none"
    return {"mode": mode, "warning": "REPLAY: orchestration checks only; NOT LLM performance" if mode.startswith("replay") else
            "Live results against agent-audited references; inspect errors and benchmark limitations",
            "total": len(records), "answerable_correct": sum(bool(r["result_correct"]) for r in answerable),
            "answerable_total": len(answerable),
            "nonanswerable_status_correct": sum(r["status_correct"] for r in nonanswerable),
            "nonanswerable_total": len(nonanswerable), "categories": groups,
            "total_model_calls": sum(r["model_calls"] for r in records),
            "mean_elapsed_ms": sum(r["elapsed_ms"] for r in records) / len(records) if records else None,
            "first_sql_executable": {"correct": sum(r.get("first_sql_executable") is True for r in answerable),
                                     "total": len(answerable)},
            "mean_necessary_table_recall": (sum(r["necessary_table_recall"] for r in records if r.get("necessary_table_recall") is not None) /
                                             max(1, sum(r.get("necessary_table_recall") is not None for r in records))),
            "judge_errors": sum("judge_error" in r for r in records)}


def write_report(records: list[dict], path: Path):
    summary = aggregate(records)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".json").write_text(json.dumps({"summary": summary, "records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Evaluation report", "", summary["warning"], "", f"Mode: {summary['mode']}", "",
             f"Answerable result matches: {summary['answerable_correct']}/{summary['answerable_total']}",
             f"Clarification/refusal status matches: {summary['nonanswerable_status_correct']}/{summary['nonanswerable_total']}",
             "", "| Case | Category | Status | Result match | First detected failure |", "|---|---|---|---|---|"]
    lines.extend(f"| {r['case_id']} | {r['category']} | {r['actual_status']} | {r['result_correct']} | {r['first_detected_failure']} |" for r in records)
    lines += ["", "Root cause requires inspecting traces or controlled upstream replacement; detected stage is not proof of root cause.",
              "Text quality was not scored unless an independent judge was explicitly configured."]
    path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
