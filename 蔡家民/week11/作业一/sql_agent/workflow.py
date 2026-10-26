from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict
from datetime import date
from pathlib import Path

from . import prompts
from .catalog import compact_catalog, complete_bridges, parse_database
from .contracts import (Check, FinalAnswer, QueryIntent, SQLCandidate, TableSelection,
                        WorkflowResult)
from .executor import ReadOnlyExecutor
from .validator import validate


def checked_strings(value, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ValueError(f"{field} must be a list of strings")
    return value


def parse_intent(raw: dict) -> QueryIntent:
    allowed = set(QueryIntent.__dataclass_fields__)
    obj = QueryIntent(**{k: v for k, v in raw.items() if k in allowed})
    if obj.status not in {"answerable", "needs_clarification", "unsupported"}:
        raise ValueError("Invalid intent status")
    for field in ("dimensions", "filters", "time_range", "assumptions", "rule_ids"):
        checked_strings(getattr(obj, field), field)
    for field in ("metric", "ranking", "grain", "clarification", "reason"):
        if not isinstance(getattr(obj, field), str):
            raise ValueError(f"{field} must be a string")
    if obj.time_range:
        if len(obj.time_range) != 2:
            raise ValueError("time_range requires inclusive start and exclusive end")
        if date.fromisoformat(obj.time_range[0]) >= date.fromisoformat(obj.time_range[1]):
            raise ValueError("Invalid time range")
    if obj.status == "needs_clarification" and not obj.clarification.strip():
        raise ValueError("Clarification status requires a question")
    return obj


def parse_candidate(raw: dict) -> SQLCandidate:
    if not isinstance(raw.get("sql"), str) or not isinstance(raw.get("params", {}), dict):
        raise ValueError("Expected SQL string and parameter object")
    return SQLCandidate(raw["sql"], raw.get("params", {}), str(raw.get("explanation", "")), str(raw.get("changes", "")))


class Workflow:
    def __init__(self, database: Path, model, rules_path: Path, artifacts: Path,
                 max_rewrites: int = 2, semantic_review: bool = True, use_rules: bool = True,
                 variant: str = "workflow", max_rows: int = 1000):
        if max_rewrites < 0 or max_rewrites > 5:
            raise ValueError("max_rewrites must be between 0 and 5")
        if variant not in {"workflow", "baseline"}:
            raise ValueError("Unknown variant")
        self.database, self.model, self.artifacts = database, model, artifacts
        self.catalog = parse_database(database, artifacts / "catalog")
        self.rules = json.loads(rules_path.read_text(encoding="utf-8")) if use_rules else {}
        self.max_rewrites = max_rewrites if variant == "workflow" else 0
        self.semantic_review = semantic_review and variant == "workflow"
        self.variant = variant
        self.executor = ReadOnlyExecutor(database, list(self.catalog["tables"]), max_rows=max_rows)

    def run(self, question: str, reference_date: str | None = None, clarification: str = "") -> WorkflowResult:
        reference_date = reference_date or date.today().isoformat()
        date.fromisoformat(reference_date)
        state = WorkflowResult(uuid.uuid4().hex, question, "running", self.model.mode, reference_date)
        base = {"question": question, "reference_date": reference_date,
                "clarification": clarification, "business_rules": self.rules}
        started = time.monotonic()
        call_start = len(self.model.calls)

        def event(node, status, payload, elapsed_ms=0):
            state.trace.append({"node": node, "status": status, "elapsed_ms": round(elapsed_ms, 3),
                                "output": payload})

        def call(node, system, context):
            before = time.monotonic()
            result = self.model.complete(node, system, context)
            event(node, "ok", result, (time.monotonic() - before) * 1000)
            return result

        try:
            event("database_parse", "ok", {"tables": list(self.catalog["tables"]),
                                          "database_sha256": self.catalog["database_sha256"]})
            catalog = compact_catalog(self.catalog)
            if self.variant == "baseline":
                state.intent = QueryIntent(metric=question)
                state.selection = TableSelection(list(catalog), reason="Baseline uses full schema")
            else:
                raw_intent = call("intent", prompts.INTENT, {**base, "catalog": catalog,
                                  "date_ranges": {n: {c["name"]: c["range"] for c in t["columns"] if "range" in c}
                                                  for n, t in self.catalog["tables"].items()}})
                state.intent = parse_intent(raw_intent)
                if state.intent.status != "answerable":
                    state.status = state.intent.status
                    state.answer = FinalAnswer(state.intent.clarification if state.status == "needs_clarification"
                                               else state.intent.reason)
                    return state
                selection = call("table_selection", prompts.TABLE, {**base, "intent": asdict(state.intent), "catalog": catalog})
                selected = checked_strings(selection.get("tables"), "tables")
                tables, bridges = complete_bridges(selected, self.catalog)
                columns = selection.get("columns", {})
                if not isinstance(columns, dict):
                    raise ValueError("Selected columns must be a dictionary")
                for table, names in columns.items():
                    if table not in tables:
                        raise ValueError(f"Column selection references unselected table {table}")
                    checked_strings(names, "columns")
                    actual = {c["name"] for c in self.catalog["tables"][table]["columns"]}
                    if set(names) - actual:
                        raise ValueError(f"Unknown columns in {table}: {set(names) - actual}")
                state.selection = TableSelection(tables, columns, checked_strings(selection.get("joins", []), "joins"),
                                                 str(selection.get("reason", "")), bridges)
                event("bridge_completion", "ok", asdict(state.selection))
            context = {**base, "intent": asdict(state.intent), "selection": asdict(state.selection),
                       "schema": {t: catalog[t] for t in state.selection.tables}}
            seen = set()
            last_error = {}
            for attempt in range(self.max_rewrites + 1):
                stage = "sql_generation" if attempt == 0 else "sql_rewrite"
                if attempt:
                    if any(c.get("error_type") == "selection_mismatch" for c in last_error.get("checks", [])):
                        updated = call("table_selection", prompts.TABLE,
                                       {**base, "intent": asdict(state.intent), "catalog": catalog,
                                        "prior_selection": asdict(state.selection), "errors": last_error})
                        tables, bridges = complete_bridges(checked_strings(updated.get("tables"), "tables"), self.catalog)
                        state.selection = TableSelection(tables, reason=str(updated.get("reason", "")), bridge_tables=bridges)
                        context.update(selection=asdict(state.selection), schema={t: catalog[t] for t in tables})
                        event("table_reselection", "ok", asdict(state.selection))
                    context.update(last_candidate=asdict(state.candidates[-1]), validation_errors=last_error,
                                   available_catalog=catalog)
                candidate = parse_candidate(call(stage, prompts.GENERATE if attempt == 0 else prompts.REWRITE, context))
                state.candidates.append(candidate)
                signature = json.dumps({"sql": candidate.sql.strip(), "params": candidate.params}, sort_keys=True)
                if signature in seen:
                    state.error = "Repeated SQL candidate; stopping repair loop"
                    event("termination", "failed", {"reason": state.error})
                    break
                seen.add(signature)
                # Baseline still enforces compile/authorization, but omits business checks.
                report = validate(candidate, state.intent, self.executor,
                                  state.selection.tables if self.variant == "workflow" else None)
                if self.variant == "baseline":
                    report.checks = [c for c in report.checks if c.name in {"query_envelope", "sqlite_compile_and_authorize"}]
                    report.passed = all(c.passed for c in report.checks)
                if report.passed and self.semantic_review:
                    review = call("semantic_review", prompts.REVIEW, {**context, "candidate": asdict(candidate)})
                    issues = review.get("issues")
                    if not isinstance(issues, list):
                        raise ValueError("Semantic review requires an issues array")
                    for issue in issues:
                        report.checks.append(Check("model_semantic_review", False, str(issue["error_type"]),
                                                   str(issue["evidence"]) + "; " + str(issue.get("repair_hint", ""))))
                    report.passed = not issues
                state.validations.append(report)
                event("sql_validation", "ok" if report.passed else "failed", asdict(report))
                if not report.passed:
                    last_error = {"checks": [asdict(c) for c in report.checks if not c.passed]}
                    if any(not c.retryable for c in report.checks if not c.passed):
                        state.error = "Forbidden operation was blocked"
                        break
                    continue
                result = self.executor.run(candidate)
                state.execution = result
                event("sql_execution", result.status, asdict(result), result.elapsed_ms)
                if result.status != "ok":
                    last_error = {"execution_error": asdict(result)}
                    if result.error_type == "forbidden_operation":
                        break
                    continue
                raw_answer = call("summary", prompts.SUMMARY, {**base, "intent": asdict(state.intent),
                                  "candidate": asdict(candidate), "execution": asdict(result)})
                if not isinstance(raw_answer.get("text"), str):
                    raise ValueError("Summary must contain text")
                answer = FinalAnswer(raw_answer["text"], checked_strings(raw_answer.get("facts", []), "facts"),
                                     checked_strings(raw_answer.get("limitations", []), "limitations"))
                if not result.rows:
                    answer.limitations.append("查询未返回记录；不能据此自动认定为0。")
                if result.truncated:
                    answer.limitations.append("结果已截断，仅展示部分数据，不代表完整明细。")
                state.answer, state.status = answer, "success"
                return state
            state.status = "failed"
            state.error = state.error or "SQL attempts exhausted: " + json.dumps(last_error, ensure_ascii=False)
        except Exception as exc:
            state.status = "failed"
            state.error = f"{type(exc).__name__}: {exc}"
            event("workflow_error", "failed", {"error": state.error})
        finally:
            state.trace.append({"node": "run_metadata", "status": state.status,
                                "output": {"variant": self.variant, "prompt_version": prompts.VERSION,
                                           "semantic_review": self.semantic_review, "rules_enabled": bool(self.rules),
                                           "max_rewrites": self.max_rewrites, "reference_date": reference_date,
                                           "database_sha256": self.catalog["database_sha256"],
                                           "calls": self.model.calls[call_start:],
                                           "elapsed_ms": (time.monotonic() - started) * 1000}})
            destination = self.artifacts / "traces" / (state.run_id + ".json")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return state
