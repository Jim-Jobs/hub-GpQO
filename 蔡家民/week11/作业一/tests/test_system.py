from __future__ import annotations

import copy
import json
import io
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sql_agent.catalog import complete_bridges, fingerprint, parse_database
from sql_agent.contracts import ExecutionResult, QueryIntent, SQLCandidate
from sql_agent.evaluation import aggregate, results_equal, validate_dataset
from sql_agent.executor import QueryRejected, ReadOnlyExecutor
from sql_agent.model import OpenAICompatibleModel, ReplayModel, parse_json
from sql_agent.validator import validate
from sql_agent.workflow import Workflow, parse_intent

ROOT = Path(__file__).resolve().parents[1]


class SystemTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = parse_database(ROOT / "data/chinook.db")
        cls.executor = ReadOnlyExecutor(ROOT / "data/chinook.db", list(cls.catalog["tables"]))
        cls.cases = json.loads((ROOT / "evaluation/datasets/seed.json").read_text(encoding="utf-8"))
        cls.fixtures = json.loads((ROOT / "evaluation/replay_fixtures.json").read_text(encoding="utf-8"))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name)

    def workflow(self, fixture=None, **kwargs):
        return Workflow(ROOT / "data/chinook.db", ReplayModel(copy.deepcopy(fixture or self.fixtures["S01"])),
                        ROOT / "config/business_rules.json", self.artifacts, **kwargs)

    def test_catalog_discovers_business_tables_and_fk(self):
        self.assertEqual(len(self.catalog["tables"]), 11)
        self.assertEqual(self.catalog["tables"]["tracks"]["row_count"], 3503)
        self.assertTrue(self.catalog["tables"]["invoice_items"]["foreign_keys"])

    def test_catalog_excludes_sensitive_samples(self):
        for column in self.catalog["tables"]["customers"]["columns"]:
            if column["name"] in {"Email", "Phone", "Fax", "Address"}:
                self.assertNotIn("samples", column)

    def test_catalog_empty_table(self):
        path = self.artifacts / "empty.db"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE empty_table (Name TEXT, InvoiceDate TEXT)")
        conn.close()
        result = parse_database(path)
        self.assertEqual(result["tables"]["empty_table"]["row_count"], 0)
        self.assertEqual(result["tables"]["empty_table"]["columns"][1]["range"], [None, None])

    def test_catalog_cache_stable(self):
        one = parse_database(ROOT / "data/chinook.db", self.artifacts)
        two = parse_database(ROOT / "data/chinook.db", self.artifacts)
        self.assertEqual(one, two)

    def test_bridge_completion_includes_intermediate_tables(self):
        tables, bridges = complete_bridges(["artists", "invoices"], self.catalog)
        self.assertTrue({"albums", "tracks", "invoice_items"}.issubset(bridges))
        self.assertEqual(len(tables), 5)

    def test_unknown_table_rejected(self):
        with self.assertRaises(ValueError):
            complete_bridges(["payments"], self.catalog)

    def test_readonly_and_named_parameters(self):
        result = self.executor.run(SQLCandidate("SELECT COUNT(*) FROM customers WHERE Country=:country", {"country": "Brazil"}))
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.rows, [[5]])

    def test_write_attach_and_metadata_denied(self):
        queries = ["DELETE FROM customers", "UPDATE customers SET FirstName='x'", "DROP TABLE customers",
                   "ATTACH DATABASE ':memory:' AS other", "PRAGMA writable_schema=1",
                   "SELECT sql FROM sqlite_master", "SELECT load_extension('x')", "SELECT randomblob(1000000000)",
                   "WITH x AS (SELECT 1) DELETE FROM customers"]
        before = fingerprint(ROOT / "data/chinook.db")
        for sql in queries:
            with self.subTest(sql=sql):
                try:
                    result = self.executor.run(SQLCandidate(sql))
                except QueryRejected:
                    continue
                self.assertEqual(result.status, "error")
        self.assertEqual(before, fingerprint(ROOT / "data/chinook.db"))

    def test_multi_statement_rejected(self):
        result = self.executor.run(SQLCandidate("SELECT COUNT(*) FROM customers; DELETE FROM customers;"))
        self.assertEqual(result.status, "error")

    def test_user_value_does_not_inject_sql(self):
        result = self.executor.run(SQLCandidate("SELECT COUNT(*) FROM customers WHERE Country=:country",
                                               {"country": "Brazil' OR 1=1 --"}))
        self.assertEqual(result.rows, [[0]])

    def test_result_truncation_is_explicit(self):
        executor = ReadOnlyExecutor(ROOT / "data/chinook.db", list(self.catalog["tables"]), max_rows=2)
        result = executor.run(SQLCandidate("SELECT TrackId FROM tracks ORDER BY TrackId"))
        self.assertTrue(result.truncated)
        self.assertEqual(len(result.rows), 2)

    def test_resource_budget_interrupts_recursive_query(self):
        executor = ReadOnlyExecutor(ROOT / "data/chinook.db", list(self.catalog["tables"]), max_vm_steps=500)
        result = executor.run(SQLCandidate("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<1000000) SELECT SUM(n) FROM x"))
        self.assertEqual(result.error_type, "resource_limit")

    def test_empty_result_null_and_zero_distinct(self):
        empty = self.executor.run(SQLCandidate("SELECT InvoiceId FROM invoices WHERE InvoiceId=-1"))
        zero = self.executor.run(SQLCandidate("SELECT COUNT(*) FROM invoices WHERE InvoiceId=-1"))
        null = self.executor.run(SQLCandidate("SELECT SUM(Total) FROM invoices WHERE InvoiceId=-1"))
        self.assertEqual((empty.rows, zero.rows, null.rows), ([], [[0]], [[None]]))

    def test_invalid_column_is_compile_error(self):
        result = validate(SQLCandidate("SELECT MissingColumn FROM customers"), QueryIntent(), self.executor)
        self.assertFalse(result.passed)
        self.assertEqual(result.checks[0].error_type, "compile_error")

    def test_header_amount_join_risk_detected(self):
        sql = "SELECT SUM(i.Total) FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId"
        result = validate(SQLCandidate(sql), QueryIntent(), self.executor)
        self.assertFalse(result.passed)
        self.assertEqual(result.checks[-1].error_type, "aggregation_grain")

    def test_correct_detail_amount_passes(self):
        sql = "SELECT SUM(ii.UnitPrice*ii.Quantity) FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId"
        self.assertTrue(validate(SQLCandidate(sql), QueryIntent(), self.executor).passed)

    def test_missing_confirmed_dates_detected(self):
        intent = QueryIntent(time_range=["2013-01-01", "2014-01-01"])
        result = validate(SQLCandidate("SELECT SUM(Total) FROM invoices"), intent, self.executor)
        self.assertFalse(result.passed)

    def test_workflow_success_and_trace(self):
        state = self.workflow().run("客户总人数是多少？", "2014-01-15")
        self.assertEqual(state.status, "success")
        self.assertEqual(state.execution.rows, [[59]])
        saved = json.loads((self.artifacts / "traces" / f"{state.run_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["mode"], "replay")
        self.assertIn("sql_execution", [e["node"] for e in saved["trace"]])

    def test_automatic_repair_preserves_error_evidence(self):
        fixture = json.loads((ROOT / "examples/repair_fixture.json").read_text(encoding="utf-8"))
        state = self.workflow(fixture).run("客户总人数是多少？", "2014-01-15")
        self.assertEqual(state.status, "success")
        self.assertEqual(len(state.candidates), 2)
        self.assertFalse(state.validations[0].passed)
        self.assertTrue(state.validations[1].passed)

    def test_repeated_bad_sql_terminates(self):
        fixture = copy.deepcopy(self.fixtures["S01"])
        fixture["candidates"] = [{"sql": "SELECT MissingColumn FROM customers"}] * 3
        state = self.workflow(fixture).run("客户数")
        self.assertEqual(state.status, "failed")
        self.assertEqual(len(state.candidates), 2)
        self.assertIn("Repeated", state.error)

    def test_forbidden_sql_never_rewritten_or_executed(self):
        fixture = copy.deepcopy(self.fixtures["S01"])
        fixture["candidates"] = [{"sql": "DELETE FROM customers"}]
        state = self.workflow(fixture).run("客户数")
        self.assertEqual(state.status, "failed")
        self.assertIsNone(state.execution)
        self.assertEqual(len(state.candidates), 1)

    def test_clarification_exits_without_sql(self):
        state = self.workflow(self.fixtures["C01"]).run("按国家统计客户消费。")
        self.assertEqual(state.status, "needs_clarification")
        self.assertFalse(state.candidates)

    def test_unsupported_exits_without_sql(self):
        state = self.workflow(self.fixtures["U01"]).run("2013年利润是多少？")
        self.assertEqual(state.status, "unsupported")
        self.assertFalse(state.candidates)

    def test_no_repair_ablation_exhausts_first_candidate(self):
        fixture = json.loads((ROOT / "examples/repair_fixture.json").read_text(encoding="utf-8"))
        state = self.workflow(fixture, max_rewrites=0).run("客户数")
        self.assertEqual(state.status, "failed")
        self.assertEqual(len(state.candidates), 1)

    def test_numeric_tolerance_and_duplicate_rows(self):
        self.assertTrue(results_equal(ExecutionResult("ok", ["a"], [[1.00000001]]), ExecutionResult("ok", ["b"], [[1]])))
        self.assertFalse(results_equal(ExecutionResult("ok", ["a"], [[1], [1]]), ExecutionResult("ok", ["b"], [[1], [2]])))

    def test_order_policy(self):
        a = ExecutionResult("ok", ["x"], [[1], [2]])
        b = ExecutionResult("ok", ["y"], [[2], [1]])
        self.assertTrue(results_equal(a, b))
        self.assertFalse(results_equal(a, b, ordered=True))

    def test_truncated_result_cannot_receive_match(self):
        a = ExecutionResult("ok", ["x"], [[1]], truncated=True)
        self.assertFalse(results_equal(a, a))

    def test_dataset_reference_queries_and_no_family_leakage(self):
        self.assertEqual(len(self.cases), 120)
        self.assertEqual(validate_dataset(self.cases, self.executor), [])

    def test_all_replay_routes(self):
        for case in self.cases:
            with self.subTest(case=case["id"]):
                state = self.workflow(self.fixtures[case["id"]]).run(case["question"], case["reference_date"])
                self.assertEqual(state.status, case["expected_status"])

    def test_json_and_intent_contracts_reject_bad_outputs(self):
        with self.assertRaises(ValueError):
            parse_json("[]")
        with self.assertRaises(ValueError):
            parse_intent({"time_range": ["2014-01-01", "2013-01-01"]})
        with self.assertRaises(ValueError):
            parse_intent({"status": "needs_clarification"})

    def test_live_requires_explicit_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                OpenAICompatibleModel(ROOT / "config/model_example.json")

    def test_selected_table_scope_mismatch_detected(self):
        result = validate(SQLCandidate("SELECT COUNT(*) FROM invoices"), QueryIntent(), self.executor, ["customers"])
        self.assertFalse(result.passed)
        self.assertTrue(any(c.error_type == "selection_mismatch" for c in result.checks))

    def test_model_http_adapter_with_mock_response(self):
        response = {"choices": [{"message": {"content": '{"status":"answerable"}'}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
        environment = {"SQL_AGENT_BASE_URL": "https://model.example/v1", "SQL_AGENT_API_KEY": "test-only",
                       "SQL_AGENT_MODEL": "test-model"}
        with patch.dict(os.environ, environment), patch("urllib.request.urlopen", return_value=io.BytesIO(json.dumps(response).encode())) as send:
            model = OpenAICompatibleModel(ROOT / "config/model_example.json")
            result = model.complete("intent", "system", {"question": "example"})
            self.assertEqual(result["status"], "answerable")
            self.assertEqual(model.calls[0]["usage"]["prompt_tokens"], 10)
            self.assertEqual(send.call_args.args[0].full_url, "https://model.example/v1/chat/completions")

    def test_per_run_usage_not_accumulated(self):
        workflow = self.workflow()
        # Reset replay's finite SQL sequence; same client may be reused by a real app.
        one = workflow.run("客户数")
        workflow.model.candidate_index = 0
        two = workflow.run("客户数")
        self.assertEqual(len(one.trace[-1]["output"]["calls"]), len(two.trace[-1]["output"]["calls"]))

    def test_plain_numeric_string_is_not_numeric_result(self):
        a = ExecutionResult("ok", ["x"], [["1"]])
        b = ExecutionResult("ok", ["x"], [[1]])
        self.assertFalse(results_equal(a, b))

    def test_unused_date_parameters_do_not_satisfy_filter(self):
        result = validate(SQLCandidate("SELECT SUM(Total) FROM invoices", {"start": "2013-01-01", "end": "2014-01-01"}),
                          QueryIntent(time_range=["2013-01-01", "2014-01-01"]), self.executor)
        self.assertFalse(result.passed)

    def test_parameter_variants_remain_in_one_split(self):
        self.assertEqual({s: sum(c["split"] == s for c in self.cases) for s in ("dev", "validation", "test")},
                         {"dev": 20, "validation": 40, "test": 60})
        families = {}
        for case in self.cases:
            families.setdefault(case["template_family"], set()).add(case["split"])
        self.assertTrue(all(len(splits) == 1 for splits in families.values()))

    def test_snapshot_drift_is_detected(self):
        sample = copy.deepcopy(self.cases[0])
        sample["reference_snapshot"]["rows"] = [[-999]]
        self.assertTrue(validate_dataset([sample], self.executor))

    def test_stage_isolation_with_reference_upstream(self):
        from sql_agent.stages import stage_evaluation
        case = next(c for c in self.cases if c["id"] == "S11")
        record = stage_evaluation(case, ReplayModel(copy.deepcopy(self.fixtures["S11"])), self.catalog, {}, self.executor)
        self.assertEqual(record["table_recall"], 1)
        self.assertTrue(record["sql_result_correct_with_gold_tables"])

    def test_controlled_replacement_localizes_table_fault(self):
        from sql_agent.stages import replacement_diagnosis
        case = next(c for c in self.cases if c["id"] == "S11")
        fixture = copy.deepcopy(self.fixtures["S11"])
        fixture["table_selection"] = {"tables": ["invoices"]}
        report = replacement_diagnosis(case, lambda: ReplayModel(copy.deepcopy(fixture)),
                                       lambda model: Workflow(ROOT / "data/chinook.db", model, ROOT / "config/business_rules.json", self.artifacts))
        self.assertFalse(report["trials"]["none"]["result_correct"])
        self.assertTrue(report["trials"]["gold_tables"]["result_correct"])

    def test_preflight_never_reads_or_prints_secret_values(self):
        from sql_agent.readiness import preflight
        with patch.dict(os.environ, {}, clear=True):
            status = preflight(ROOT)
        self.assertFalse(status["ready_for_live"])
        self.assertFalse(status["network_called"])
        self.assertEqual(len(status["missing_environment"]), 3)

    def test_stage_benchmark_has_30_distinct_cases(self):
        from sql_agent.diagnostics import validation_cases
        cases = validation_cases()
        self.assertEqual(len(cases), 30)
        self.assertEqual(len({(c["sql"], json.dumps(c["intent"], sort_keys=True)) for c in cases}), 30)

    def test_model_judge_failure_does_not_erase_query_result(self):
        from sql_agent.evaluation import evaluate_case
        class BadJudge:
            model = "bad-test-judge"
            def complete(self, *args):
                return {"faithfulness": 99}
        case = self.cases[0]
        record = evaluate_case(case, self.workflow(self.fixtures[case["id"]]), BadJudge())
        self.assertTrue(record["result_correct"])
        self.assertIn("judge_error", record)

    def test_shared_request_budget_prevents_extra_model_call(self):
        from scripts.run_experiments import BudgetModel
        wrapped = ReplayModel(copy.deepcopy(self.fixtures["S01"]))
        budget = {"used": 0, "limit": 1}
        model = BudgetModel(wrapped, budget)
        model.complete("intent", "", {})
        with self.assertRaises(RuntimeError):
            model.complete("intent", "", {})
        self.assertEqual(len(wrapped.calls), 1)

    def test_http_failure_preserves_attempt_without_printing_key(self):
        import urllib.error
        environment = {"SQL_AGENT_BASE_URL": "https://model.example/v1", "SQL_AGENT_API_KEY": "private-test-marker",
                       "SQL_AGENT_MODEL": "test-model"}
        with patch.dict(os.environ, environment), patch("urllib.request.urlopen", side_effect=urllib.error.URLError("blocked")):
            model = OpenAICompatibleModel(ROOT / "config/model_example.json")
            with self.assertRaises(RuntimeError) as error:
                model.complete("intent", "", {})
        self.assertEqual(len(model.calls), 1)
        self.assertNotIn("private-test-marker", str(error.exception) + json.dumps(model.calls))

    def test_notebook_offline_cells_run_without_model(self):
        notebook = json.loads((ROOT / "notebooks/demo.ipynb").read_text(encoding="utf-8"))
        scope = {}
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                exec(compile("".join(cell["source"]), "demo.ipynb", "exec"), scope)
        self.assertEqual(scope["state"].status, "success")

    def test_replay_metrics_explicitly_not_model_scores(self):
        report = aggregate([{"mode": "replay", "expected_status": "success", "result_correct": True,
                             "category": "single", "task_correct": True, "model_calls": 0, "elapsed_ms": 1}])
        self.assertIn("NOT LLM performance", report["warning"])


if __name__ == "__main__":
    unittest.main()
