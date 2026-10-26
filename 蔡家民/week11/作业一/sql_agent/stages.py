"""Oracle-input evaluation and controlled replacement for failure localization."""
from __future__ import annotations

import copy
from dataclasses import asdict

from . import prompts
from .catalog import compact_catalog, complete_bridges
from .contracts import SQLCandidate
from .evaluation import results_equal
from .workflow import parse_candidate


class OracleModel:
    def __init__(self, wrapped, replacements: dict):
        self.wrapped, self.replacements = wrapped, replacements
        self.mode = wrapped.mode + "_oracle"
        self.model = wrapped.model
        self.calls = []

    def complete(self, stage, system, context):
        if stage in self.replacements:
            self.calls.append({"stage": stage, "model": "verified-reference-intervention", "usage": None, "source": "oracle"})
            return copy.deepcopy(self.replacements[stage])
        before = len(self.wrapped.calls)
        try:
            return self.wrapped.complete(stage, system, context)
        finally:
            self.calls.extend(self.wrapped.calls[before:])


def stage_evaluation(case, model, catalog, rules, executor, judge=None):
    """Independent nodes see verified upstream inputs, never labeled as end-to-end."""
    record = {"case_id": case["id"], "mode": model.mode, "protocol": "oracle-input independent nodes"}
    if case["expected_status"] != "success":
        intent = model.complete("intent", prompts.INTENT, {"question": case["question"], "reference_date": case["reference_date"],
                                "catalog": compact_catalog(catalog), "business_rules": rules})
        record["intent_status_correct"] = intent.get("status") == case["expected_status"]
        return record
    base = {"question": case["question"], "reference_date": case["reference_date"],
            "intent": case["gold_intent"], "business_rules": rules}
    try:
        selected = model.complete("table_selection", prompts.TABLE, {**base, "catalog": compact_catalog(catalog)})
        tables, _ = complete_bridges(selected["tables"], catalog)
        required = set(case["required_tables"])
        record.update(table_recall=len(required & set(tables)) / len(required), extra_tables=len(set(tables) - required))
    except Exception as exc:
        record["table_error"] = str(exc)
    try:
        raw = model.complete("sql_generation", prompts.GENERATE,
                             {**base, "selection": {"tables": case["required_tables"]},
                              "schema": {t: catalog["tables"][t] for t in case["required_tables"]}})
        candidate = parse_candidate(raw)
        actual = executor.run(candidate)
        reference = executor.run(SQLCandidate(case["reference_sql"], case.get("params", {})))
        record["sql_result_correct_with_gold_tables"] = results_equal(actual, reference, case.get("ordered", False))
        record["sql_status"] = actual.status
    except Exception as exc:
        record["sql_result_correct_with_gold_tables"] = False
        record["sql_error"] = str(exc)
    try:
        reference = executor.run(SQLCandidate(case["reference_sql"], case.get("params", {})))
        answer = model.complete("summary", prompts.SUMMARY, {**base, "candidate": {"sql": case["reference_sql"], "params": case.get("params", {})},
                                                            "execution": asdict(reference)})
        record["summary_with_gold_result"] = answer
        if judge:
            record["summary_judgment"] = judge.complete("judge", prompts.JUDGE,
                {"question": case["question"], "reference": asdict(reference), "answer": answer.get("text", "")})
    except Exception as exc:
        record["summary_error"] = str(exc)
    return record


def replacement_diagnosis(case, model_factory, workflow_factory):
    """Each trial starts fresh. Recovery is evidence, not proof of a unique root cause."""
    if case["expected_status"] != "success":
        return {"case_id": case["id"], "skipped": "Only answerable cases support SQL replacements"}
    trials = {}
    interventions = {
        "none": {},
        "gold_intent": {"intent": case["gold_intent"]},
        "gold_tables": {"intent": case["gold_intent"], "table_selection": {"tables": case["required_tables"]}},
        "gold_sql": {"intent": case["gold_intent"], "table_selection": {"tables": case["required_tables"]},
                     "sql_generation": {"sql": case["reference_sql"], "params": case.get("params", {})}},
    }
    for name, replacements in interventions.items():
        model = OracleModel(model_factory(), replacements)
        flow = workflow_factory(model)
        state = flow.run(case["question"], case["reference_date"], case.get("clarification", ""))
        expected = flow.executor.run(SQLCandidate(case["reference_sql"], case.get("params", {})))
        trials[name] = {"status": state.status, "run_id": state.run_id,
                       "result_correct": bool(state.status == "success" and state.execution and
                                              results_equal(state.execution, expected, case.get("ordered", False))),
                       "answer": asdict(state.answer) if state.answer else None,
                       "error": state.error, "interventions": list(replacements)}
    return {"case_id": case["id"], "protocol": "controlled upstream replacement; not ordinary end-to-end scores", "trials": trials,
            "note": "Compare answers separately. Restoring SQL results does not prove summary quality or a unique cause."}
