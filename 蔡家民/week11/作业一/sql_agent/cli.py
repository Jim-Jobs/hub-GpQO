from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .catalog import parse_database
from .evaluation import evaluate_case, validate_dataset, write_report
from .executor import ReadOnlyExecutor
from .model import OpenAICompatibleModel, ReplayModel
from .workflow import Workflow

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Chinook SQL Agent: live workflow / explicit offline replay")
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("catalog")
    commands.add_parser("check-dataset")
    commands.add_parser("audit")
    commands.add_parser("diagnostics")
    commands.add_parser("preflight")
    ask = commands.add_parser("ask")
    ask.add_argument("question")
    ask.add_argument("--reference-date", default=None)
    ask.add_argument("--clarification", default="")
    ask.add_argument("--replay-case", default=None)
    ask.add_argument("--fixture", type=Path)
    evaluate = commands.add_parser("evaluate")
    evaluate.add_argument("--mode", choices=["live", "replay"], required=True)
    evaluate.add_argument("--split", choices=["dev", "validation", "test", "all"], default="dev")
    evaluate.add_argument("--limit", type=int)
    evaluate.add_argument("--report", default="evaluation")
    evaluate.add_argument("--judge-config", type=Path)
    stages = commands.add_parser("stages")
    stages.add_argument("--mode", choices=["live", "replay"], required=True)
    stages.add_argument("--split", choices=["dev", "validation", "test", "all"], default="dev")
    stages.add_argument("--limit", type=int, default=3)
    stages.add_argument("--diagnose-case")
    stages.add_argument("--judge-config", type=Path)
    for command in (ask, evaluate):
        command.add_argument("--variant", choices=["workflow", "baseline"], default="workflow")
        command.add_argument("--no-review", action="store_true")
        command.add_argument("--no-rules", action="store_true")
        command.add_argument("--max-rewrites", type=int, default=2)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    database = root / "data/chinook.db"
    if args.command == "preflight":
        from .readiness import preflight
        result = preflight(root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ready_for_live"] else 2
    if args.command == "catalog":
        catalog = parse_database(database, root / "artifacts/catalog")
        print(json.dumps(catalog, ensure_ascii=False, indent=2))
        return 0
    cases = load(root / "evaluation/datasets/seed.json")
    manifest = load(root / "evaluation/datasets/manifest.json")
    if manifest.get("dataset_sha256") != hashlib.sha256((root / "evaluation/datasets/seed.json").read_bytes()).hexdigest():
        raise ValueError("Dataset has changed without updating its manifest/version")
    catalog = parse_database(database)
    if any(c.get("database_sha256") != catalog["database_sha256"] for c in cases):
        raise ValueError("Database fingerprint differs from dataset; regenerate/audit references explicitly")
    if args.command == "check-dataset":
        errors = validate_dataset(cases, ReadOnlyExecutor(database, list(catalog["tables"])))
        print(json.dumps({"cases": len(cases), "errors": errors, "review": manifest["review_status"]}, ensure_ascii=False, indent=2))
        return bool(errors)
    if args.command == "audit":
        from .audit import audit_dataset
        result = audit_dataset(cases, ReadOnlyExecutor(database, list(catalog["tables"])), root)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return bool(result["errors"])
    if args.command == "diagnostics":
        from .diagnostics import run_counterexamples, run_validation_benchmark
        validation, counterexamples = run_validation_benchmark(root), run_counterexamples(root)
        print(json.dumps({"validation": {k: v for k, v in validation.items() if k != "records"},
                          "counterexamples": counterexamples}, ensure_ascii=False, indent=2))
        return int(validation["classification_correct"] != 30 or counterexamples["passed"] != counterexamples["total"] or
                   validation["scripted_repair"]["successful"] != validation["scripted_repair"]["total"])

    if args.command == "stages":
        from .stages import replacement_diagnosis, stage_evaluation
        if args.mode == "replay" and args.judge_config:
            raise ValueError("Replay scores must not use a live judge")
        if args.limit < 1:
            raise ValueError("limit must be positive")
        fixtures = load(root / "evaluation/replay_fixtures.json")
        def model_for(case):
            return ReplayModel(fixtures[case["id"]]) if args.mode == "replay" else OpenAICompatibleModel(root / "config/model_example.json")
        def make_flow(model):
            return Workflow(database, model, root / "config/business_rules.json", root / "artifacts/stages")
        judge = OpenAICompatibleModel(args.judge_config) if args.judge_config else None
        if args.diagnose_case:
            case = next((c for c in cases if c["id"] == args.diagnose_case), None)
            if case is None:
                raise ValueError("Unknown diagnosis case")
            output = replacement_diagnosis(case, lambda: model_for(case), make_flow)
            destination = root / "reports" / f"diagnosis_{args.mode}_{case['id']}.json"
        else:
            selected = [c for c in cases if args.split == "all" or c["split"] == args.split][:args.limit]
            output = {"mode": args.mode, "warning": "Oracle-input independent stages, not end-to-end scores",
                      "records": [stage_evaluation(c, model_for(c), catalog, load(root / "config/business_rules.json"),
                                                   ReadOnlyExecutor(database, list(catalog["tables"])), judge) for c in selected]}
            destination = root / "reports" / f"stages_{args.mode}_{args.split}.json"
        destination.parent.mkdir(exist_ok=True)
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print("Report:", destination)
        return 0

    def workflow(model):
        return Workflow(database, model, root / "config/business_rules.json", root / "artifacts",
                        max_rewrites=args.max_rewrites, semantic_review=not args.no_review,
                        use_rules=not args.no_rules, variant=args.variant)

    if args.command == "ask":
        if args.fixture:
            model = ReplayModel(load(args.fixture))
        elif args.replay_case:
            case = next(c for c in cases if c["id"] == args.replay_case)
            if args.question != case["question"]:
                raise ValueError("Replay question must exactly match the selected fixture case")
            model = ReplayModel(load(root / "evaluation/replay_fixtures.json")[case["id"]])
        else:
            model = OpenAICompatibleModel(root / "config/model_example.json")
        state = workflow(model).run(args.question, args.reference_date, args.clarification)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return int(state.status == "failed")
    selected = [c for c in cases if args.split == "all" or c["split"] == args.split]
    if Path(args.report).name != args.report or not args.report.strip() or "." in args.report:
        raise ValueError("report must be a simple filename without extension")
    if args.limit is not None:
        if args.limit < 1:
            raise ValueError("limit must be positive")
        selected = selected[:args.limit]
    fixtures = load(root / "evaluation/replay_fixtures.json") if args.mode == "replay" else None
    if args.mode == "replay" and args.judge_config:
        raise ValueError("Do not score replay fixtures with a live judge as model performance")
    judge = OpenAICompatibleModel(args.judge_config) if args.judge_config else None
    records = []
    for case in selected:
        model = ReplayModel(fixtures[case["id"]]) if fixtures is not None else OpenAICompatibleModel(root / "config/model_example.json")
        record = evaluate_case(case, workflow(model), judge)
        records.append(record)
        write_report(records, root / "reports" / args.report)
        print(case["id"], record["actual_status"], "result_match=", record["result_correct"], flush=True)
    path = root / "reports" / args.report
    write_report(records, path)
    print("Report:", path.with_suffix(".md"))
    return 0


def entrypoint():
    try:
        return main()
    except (ValueError, KeyError, OSError, RuntimeError) as exc:
        print(f"Configuration/input error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
