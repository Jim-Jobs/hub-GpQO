#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一键编排: 把抓取之后的分析链路串成一条命令。

设计取舍: 抓取阶段耗时长(列表 25 分钟 + 详情 50 分钟)、需要断点续跑,
不适合塞进 pipeline 里盲目重跑。这里只编排「清洗 -> 统计 -> 渲染」,
抓取仍由 ccgp_crawler.py / daily.py 各自负责。

用法:
  # 历史分析 (自动找 raw/ 下最新的 notices_*.json)
  python pipeline.py history --data ./esg_bid_data --period "2024-11-01 ~ 2026-11-24"
  python pipeline.py history --data ./esg_bid_data --narrative ./insight.md

  # 每日监控
  python pipeline.py daily --data ./esg_bid_data --days 1

  # 查看各阶段产物是否齐全
  python pipeline.py status --data ./esg_bid_data

所有子命令都用 sys.executable 调用, 保证与当前 conda 环境一致。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).parent.resolve()


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def run(script: str, args: list[str]) -> int:
    cmd = [sys.executable, str(HERE / script)] + [str(a) for a in args]
    log("运行: " + " ".join(cmd[1:]))
    # 不切换 cwd: 调用方传入的相对路径(--raw / --narrative / --data)应相对于
    # 用户当前工作目录解析, 切到 skill 目录会让这些路径全部失效。
    r = subprocess.run(cmd)
    if r.returncode != 0:
        log(f"!! {script} 退出码 {r.returncode}，流水线中止")
    return r.returncode


def find_raw(data: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"指定的 --raw 不存在: {p}")
        return p
    cands = sorted(
        (p for p in (data / "raw").glob("notices_*.json") if p.is_file()),
        key=lambda p: p.stat().st_mtime, reverse=True)
    if not cands:
        raise SystemExit(
            f"未在 {data / 'raw'} 找到 notices_*.json。\n"
            "请先用 ccgp_crawler.py 抓详情 (加 --from-cache 复用列表结果)。")
    if len(cands) > 1:
        log(f"发现 {len(cands)} 个 notices 文件，取最新的 {cands[0].name}")
    return cands[0]


def cmd_history(args) -> int:
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    raw = find_raw(data, args.raw)

    if run("normalize.py", ["--raw", raw, "--out", data]) != 0:
        return 1
    if run("analyze.py", ["--data", data]) != 0:
        return 1

    rargs = ["--data", data]
    if args.period:
        rargs += ["--period", args.period]
    if args.title:
        rargs += ["--title", args.title]
    if args.narrative:
        rargs += ["--narrative", args.narrative]
    if run("render_report.py", rargs) != 0:
        return 1

    report = data / "report"
    log("完成。产物:")
    for f in sorted(report.glob("*")):
        if f.is_file():
            log(f"  {f}  ({f.stat().st_size / 1024:.0f} KB)")
    return 0


def cmd_daily(args) -> int:
    data = Path(args.data).resolve()
    data.mkdir(parents=True, exist_ok=True)
    dargs = ["--data", data, "--days", args.days]
    if args.force:
        dargs.append("--force")
    if args.no_crawl:
        dargs.append("--no-crawl")
    if args.raw:
        dargs += ["--raw", args.raw]
    return run("daily.py", dargs)


def cmd_status(args) -> int:
    data = Path(args.data).resolve()
    checks = [
        ("列表缓存", data / "cache" / "list.jsonl"),
        ("列表去重结果", data / "raw" / "list_unique.json"),
        ("项目明细", data / "projects.json"),
        ("统计结果", data / "stats.json"),
        ("HTML 报告", data / "report" / "esg_bid_report.html"),
        ("Excel 台账", data / "report" / "esg_bid_ledger.xlsx"),
        ("每日简报目录", data / "briefs"),
    ]
    raws = sorted((data / "raw").glob("notices_*.json")) if (data / "raw").exists() else []
    log(f"数据目录: {data}")
    if raws:
        for p in raws:
            log(f"  详情文件 {p.name}  ({p.stat().st_size / 1024:.0f} KB)")
    n_detail = len(list((data / "cache" / "detail").glob("*.txt"))) if (data / "cache" / "detail").exists() else 0
    log(f"  详情缓存 {n_detail} 条")
    for name, p in checks:
        if p.is_dir():
            n = len(list(p.iterdir()))
            log(f"  {'OK ' if n else '-- '} {name}: {n} 个文件")
        else:
            log(f"  {'OK ' if p.exists() else '-- '} {name}: "
                + (f"{p.stat().st_size / 1024:.0f} KB" if p.exists() else "缺失"))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ESG 投标情报一键编排")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("history", help="清洗 -> 统计 -> 渲染历史报告")
    h.add_argument("--data", default="./esg_bid_data")
    h.add_argument("--raw", default=None, help="指定 notices_*.json，缺省自动取最新")
    h.add_argument("--period", default="")
    h.add_argument("--title", default="")
    h.add_argument("--narrative", default=None, help="外部洞察稿 Markdown")

    d = sub.add_parser("daily", help="每日增量监控与简报")
    d.add_argument("--data", default="./esg_bid_data")
    d.add_argument("--days", type=int, default=1)
    d.add_argument("--force", action="store_true")
    d.add_argument("--no-crawl", action="store_true")
    d.add_argument("--raw", default=None)

    s = sub.add_parser("status", help="检查各阶段产物")
    s.add_argument("--data", default="./esg_bid_data")

    args = ap.parse_args()
    return {"history": cmd_history, "daily": cmd_daily, "status": cmd_status}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
