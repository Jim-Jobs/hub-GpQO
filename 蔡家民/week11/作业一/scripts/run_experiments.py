"""Plan by default. Explicit --execute is required for model-bearing experiments."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sql_agent.catalog import fingerprint
from sql_agent.evaluation import evaluate_case, write_report
from sql_agent.model import OpenAICompatibleModel, ReplayModel
from sql_agent.readiness import preflight
from sql_agent.workflow import Workflow


class BudgetModel:
    def __init__(self, wrapped, budget):
        self.wrapped, self.budget = wrapped, budget
        self.mode, self.model = wrapped.mode, wrapped.model
        self.calls = []

    def complete(self, stage, system, context):
        if self.budget["used"] >= self.budget["limit"]:
            raise RuntimeError("Shared request budget exhausted; no further request sent")
        self.budget["used"] += 1
        before = len(self.wrapped.calls)
        try:
            return self.wrapped.complete(stage, system, context)
        finally:
            self.calls.extend(self.wrapped.calls[before:])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["live", "replay"], required=True)
    parser.add_argument("--split", choices=["dev", "validation", "test"], default="dev")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--variants", nargs="+", choices=["baseline", "workflow", "no_rewrite", "no_review"],
                        default=["baseline", "workflow"])
    parser.add_argument("--max-requests", type=int, default=60)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if not (1 <= args.limit <= 120 and 1 <= args.repeats <= 10 and args.max_requests > 0):
        raise ValueError("Invalid sample/repeat/request limit")
    manifest = json.loads((ROOT / "evaluation/datasets/manifest.json").read_text(encoding="utf-8"))
    if manifest["dataset_sha256"] != fingerprint(ROOT / "evaluation/datasets/seed.json"):
        raise ValueError("Dataset fingerprint differs from the frozen manifest")
    if manifest["database_sha256"] != fingerprint(ROOT / "data/chinook.db"):
        raise ValueError("Database differs from the reference dataset")
    cases = json.loads((ROOT / "evaluation/datasets/seed.json").read_text(encoding="utf-8"))
    cases = [c for c in cases if c["split"] == args.split][:args.limit]
    plan = {"mode": args.mode, "split": args.split, "case_ids": [c["id"] for c in cases],
            "variants": list(dict.fromkeys(args.variants)), "repeats": args.repeats,
            "max_requests": args.max_requests, "database_sha256": fingerprint(ROOT / "data/chinook.db"),
            "dataset_sha256": fingerprint(ROOT / "evaluation/datasets/seed.json"),
            "executed": args.execute,
            "warning": "Replay does not measure model quality. Live runs incur provider usage."}
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        print("Plan only. Add --execute to run.")
        return 0
    if args.mode == "live" and not preflight(ROOT)["ready_for_live"]:
        print(json.dumps(preflight(ROOT), ensure_ascii=False, indent=2))
        return 2
    fixtures = json.loads((ROOT / "evaluation/replay_fixtures.json").read_text(encoding="utf-8")) if args.mode == "replay" else None
    budget = {"used": 0, "limit": args.max_requests}
    results = []
    incomplete = False
    for variant in plan["variants"]:
        for repeat in range(1, args.repeats + 1):
            records = []
            name = f"experiment_{args.mode}_{args.split}_{variant}_r{repeat}"
            for case in cases:
                if budget["used"] >= budget["limit"]:
                    incomplete = True
                    break
                model = ReplayModel(fixtures[case["id"]]) if fixtures is not None else OpenAICompatibleModel(ROOT / "config/model_example.json")
                model = BudgetModel(model, budget)
                flow = Workflow(ROOT / "data/chinook.db", model, ROOT / "config/business_rules.json", ROOT / "artifacts/experiments",
                                variant="baseline" if variant == "baseline" else "workflow",
                                semantic_review=variant != "no_review", max_rewrites=0 if variant == "no_rewrite" else 2)
                record = evaluate_case(case, flow)
                records.append(record)
                write_report(records, ROOT / "reports" / name)
                if "budget exhausted" in record.get("error", "").lower():
                    incomplete = True
                    break
            results.append({"variant": variant, "repeat": repeat, "records": records})
            if incomplete:
                break
        if incomplete:
            break
    report = {"plan": plan, "requests": budget, "incomplete": incomplete, "results": results}
    (ROOT / "reports" / f"experiment_{args.mode}_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # Repeatability is only observed on the sampled records. Do not call it universal determinism.
    stable = {}
    for variant in plan["variants"]:
        for case in cases:
            values = [r["task_correct"] for group in results if group["variant"] == variant for r in group["records"] if r["case_id"] == case["id"]]
            stable[f"{variant}:{case['id']}"] = {"observed": len(values), "consistent": len(set(values)) <= 1,
                                                   "correct_runs": sum(bool(v) for v in values)}
    (ROOT / "reports" / f"stability_{args.mode}.json").write_text(json.dumps({"mode": args.mode, "cases": stable}, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(incomplete)


if __name__ == "__main__":
    raise SystemExit(main())
