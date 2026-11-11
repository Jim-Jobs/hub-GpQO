"""批量评测：调用运行中的 /chat，按文件名/页码/答案 Jaccard 计分。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from app.evaluation import load_cases, score_case  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG evaluation")
    parser.add_argument(
        "--dataset",
        default=str(ROOT / "tests" / "fixtures" / "sample_eval.jsonl"),
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--knowledge-base", default="default")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    cases = load_cases(args.dataset)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("no evaluation cases")
        return 1

    headers = {"X-API-Key": args.api_key} if args.api_key else {}
    results = []
    for index, case in enumerate(cases, start=1):
        try:
            response = httpx.post(
                f"{args.base_url.rstrip('/')}/chat",
                json={
                    "question": case.question,
                    "knowledge_base_id": args.knowledge_base,
                },
                headers=headers,
                timeout=60,
            )
            response.raise_for_status()
            scored = score_case(case, response.json())
        except Exception as exc:
            print(f"[{index}] error: {exc}")
            results.append({"total_score": 0.0})
            continue

        results.append(scored)
        print(
            f"[{index}] total={scored['total_score']:.3f} "
            f"filename={scored['filename_score']:.0f} "
            f"page={scored['page_score']:.0f} "
            f"content={scored['content_score']:.3f} "
            f"| {case.question}"
        )

    average = sum(item.get("total_score", 0.0) for item in results) / len(results)
    print(json.dumps({"cases": len(results), "average": round(average, 4)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
