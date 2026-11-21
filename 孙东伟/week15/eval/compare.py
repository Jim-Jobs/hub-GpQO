"""对比实验：同一批语料分别跑 LangGraph 与 Agents SDK，输出可贴进报告的表格。

用法：
    python -m eval.compare                 # 有 Key 跑真实模型，没有 Key 自动 mock
    python -m eval.compare --engine agents # 只跑一个引擎
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from app.config import settings
from app.engines.registry import build_engines

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases.jsonl"
REPORT = ROOT / "report.md"


def load_cases() -> list[dict]:
    return [json.loads(line) for line in CASES.read_text(encoding="utf-8").splitlines() if line.strip()]


async def evaluate(only: str | None = None) -> tuple[list[dict], dict]:
    engines = build_engines()
    if only:
        engines = {only: engines[only]}

    rows: list[dict] = []
    for case in load_cases():
        for name, engine in engines.items():
            started = time.perf_counter()
            result = await engine.run(case["text"])
            rows.append(
                {
                    "id": case["id"],
                    "text": case["text"],
                    "expect": case["expect"],
                    "engine": name,
                    "label": result.sentiment.label.value,
                    "confidence": result.sentiment.confidence,
                    "crisis": result.crisis,
                    "latency_ms": int((time.perf_counter() - started) * 1000),
                    "reply": result.reply,
                }
            )
    return rows, engines


def summarize(rows: list[dict]) -> dict:
    summary: dict[str, dict] = {}
    for name in sorted({row["engine"] for row in rows}):
        subset = [row for row in rows if row["engine"] == name]
        matched = sum(row["label"] == row["expect"] for row in subset)
        summary[name] = {
            "accuracy": matched / len(subset),
            "avg_latency": statistics.mean(row["latency_ms"] for row in subset),
            "crisis_hits": sum(row["crisis"] for row in subset),
        }
    return summary


def render(rows: list[dict], summary: dict, mode: str) -> str:
    lines = [
        "# 双引擎对比实验",
        "",
        f"- 生成时间：{datetime.now():%Y-%m-%d %H:%M}",
        f"- 运行模式：`{mode}`（`mock` 为离线规则引擎，不代表真实模型能力）",
        f"- 模型：`{settings.model}`",
        "",
        "## 汇总",
        "",
        "| 引擎 | 标签准确率 | 平均耗时 | 危机命中 |",
        "| --- | --- | --- | --- |",
    ]
    for name, item in summary.items():
        lines.append(
            f"| {name} | {item['accuracy']:.0%} | {item['avg_latency']:.0f} ms | {item['crisis_hits']} |"
        )

    lines += ["", "## 逐条结果", "", "| # | 输入 | 期望 | 引擎 | 实际 | 置信度 | 耗时 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        text = row["text"].replace("|", "\\|")
        lines.append(
            f"| {row['id']} | {text} | {row['expect']} | {row['engine']} | {row['label']} | "
            f"{row['confidence']:.2f} | {row['latency_ms']} ms |"
        )
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", choices=["langgraph", "agents"], default=None)
    args = parser.parse_args()

    rows, _ = await evaluate(args.engine)
    summary = summarize(rows)
    report = render(rows, summary, settings.mode)
    REPORT.write_text(report, encoding="utf-8")
    print(report)
    print(f"报告已写入 {REPORT}")


if __name__ == "__main__":
    asyncio.run(main())
