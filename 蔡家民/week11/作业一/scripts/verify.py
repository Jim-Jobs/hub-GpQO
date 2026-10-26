"""Reproducible local checks; never makes live model requests."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    environment = dict(os.environ, PYTHONIOENCODING="utf-8")
    commands = [
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
        [sys.executable, "-m", "sql_agent", "check-dataset"],
        [sys.executable, "-m", "sql_agent", "audit"],
        [sys.executable, "-m", "sql_agent", "diagnostics"],
        [sys.executable, "-m", "sql_agent", "evaluate", "--mode", "replay", "--split", "all", "--report", "replay_smoke"],
        [sys.executable, "-m", "sql_agent", "stages", "--mode", "replay", "--split", "all", "--limit", "120"],
        [sys.executable, "-m", "sql_agent", "stages", "--mode", "replay", "--diagnose-case", "S11"],
        [sys.executable, "scripts/run_experiments.py", "--mode", "replay", "--split", "dev", "--limit", "3",
         "--repeats", "3", "--variants", "baseline", "workflow", "no_rewrite", "--max-requests", "300", "--execute"],
    ]
    records = []
    for index, command in enumerate(commands):
        completed = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8")
        record = {"check": index + 1, "arguments": command[1:], "returncode": completed.returncode,
                  "stdout": completed.stdout, "stderr": completed.stderr}
        records.append(record)
        print(f"Check {index + 1}: {'PASS' if completed.returncode == 0 else 'FAIL'}")
        if completed.returncode:
            print(completed.stderr[-5000:])
            break
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "verification.json").write_text(json.dumps({"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                                                          "python_version": sys.version, "checks": records},
                                                         ensure_ascii=False, indent=2), encoding="utf-8")
    return int(len(records) != len(commands) or any(r["returncode"] for r in records))


if __name__ == "__main__":
    raise SystemExit(main())
