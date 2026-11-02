"""Web 端性能基准：对运行中的服务发 SSE 请求，测量各阶段延迟。

用法（需先 `python server.py` 启动服务）：
    python tools/bench_web.py                    # 内置 3 道题（易/中/难）
    python tools/bench_web.py "你的问题"          # 自定义（可多题）
    python tools/bench_web.py --url http://127.0.0.1:8080 --repeat 2
    python tools/bench_web.py --fast --cache          # 允许命中问答缓存（对比收益）

注：默认请求带 nocache=true，测真实 LLM 延迟；只有 --cache 才允许命中缓存。

指标：
    首事件   连接 + get_schema 全部就绪（页面出现第一个进度节点）
    SQL      生成 SQL 完成（页面出现 SQL 代码块）
    结果     执行完成（页面出现结果表格）
    首 token 回答第一个字到达（TTFT，用户最直观的"卡不卡"指标）
    总耗时   完整链路结束
    事件数   SSE 事件总数 / token 事件数（越少 → 前端渲染压力越小）
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

DEFAULT_URL = "http://127.0.0.1:8000"
QUESTIONS = [
    "数据库里一共有多少位客户？",                 # 易：单表聚合
    "每种音乐流派各有多少首歌曲？",                 # 中：JOIN + 分组
    "销量最高的前5位艺术家是谁？",                 # 难：四表 JOIN
]


def bench_once(client: httpx.Client, url: str, question: str,
               thread_id: str, fast: bool = False, nocache: bool = False) -> dict:
    """跑一次问答，返回各阶段耗时与事件统计。"""
    t0 = time.perf_counter()
    marks: dict[str, float] = {}
    counts: dict[str, int] = {}
    answer_len = 0
    error = ""

    with client.stream(
        "POST", f"{url}/api/chat",
        json={"question": question, "thread_id": thread_id, "fast": fast,
              "nocache": nocache},
        timeout=120,
    ) as resp:
        resp.raise_for_status()
        event = "message"
        for line in resp.iter_lines():
            now = time.perf_counter() - t0
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
                continue
            if not line.startswith("data:"):
                continue
            counts[event] = counts.get(event, 0) + 1
            try:
                data = json.loads(line[len("data:"):])
            except json.JSONDecodeError:
                continue

            if "首事件" not in marks:
                marks["首事件"] = now
            if event == "sql" and "SQL" not in marks:
                marks["SQL"] = now
            if event == "result" and "结果" not in marks:
                marks["结果"] = now
            if event == "token" and "首 token" not in marks:
                marks["首 token"] = now
            if event == "token":
                answer_len += len(data.get("text", ""))
            if event == "done":
                marks["总耗时"] = now
                if not data.get("answer"):
                    error = "空回答"
            if event == "error":
                error = data.get("message", "")

    total = marks.get("总耗时", time.perf_counter() - t0)
    return {
        "question": question,
        "首事件": round(marks.get("首事件", 0), 2),
        "SQL": round(marks.get("SQL", 0), 2),
        "结果": round(marks.get("结果", 0), 2),
        "首 token": round(marks.get("首 token", 0), 2),
        "总耗时": round(total, 2),
        "事件数": sum(counts.values()),
        "token 事件": counts.get("token", 0),
        "回答字数": answer_len,
        "错误": error,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Web 端 SSE 性能基准")
    ap.add_argument("questions", nargs="*", help="自定义问题（默认内置 3 道）")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--repeat", type=int, default=1, help="每道题重复次数")
    ap.add_argument("--fast", action="store_true", help="极速模式（跳过评审回路）")
    ap.add_argument("--cache", action="store_true",
                     help="允许命中问答缓存（默认禁用，以测真实 LLM 延迟）")
    args = ap.parse_args()

    questions = args.questions or QUESTIONS
    url = args.url.rstrip("/")

    try:
        httpx.get(f"{url}/api/health", timeout=5).raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"无法连接服务 {url}（{e}）。请先运行：python server.py")
        sys.exit(1)

    mode = "极速模式（跳过评审）" if args.fast else "严谨模式（评审把关）"
    print(f"\nWeb 性能基准 · {url} · {mode}\n")
    rows = []
    with httpx.Client(timeout=120) as client:
        for i, q in enumerate(questions, 1):
            for r in range(args.repeat):
                tid = f"bench-{i}-{r}"
                row = bench_once(client, url, q, tid, fast=args.fast,
                                 nocache=not args.cache)
                rows.append(row)
                flag = " ❌" if row["错误"] else ""
                print(f"  [{q[:22]:<24}] 首事件 {row['首事件']:>5.2f}s | "
                      f"SQL {row['SQL']:>5.2f}s | 结果 {row['结果']:>5.2f}s | "
                      f"首token {row['首 token']:>5.2f}s | 总 {row['总耗时']:>5.2f}s | "
                      f"事件 {row['事件数']:>3} (token {row['token 事件']:>3}){flag}")

    n = len(rows)
    print(f"\n  平均（{n} 次）："
          f"首事件 {sum(r['首事件'] for r in rows) / n:.2f}s · "
          f"SQL {sum(r['SQL'] for r in rows) / n:.2f}s · "
          f"首token {sum(r['首 token'] for r in rows) / n:.2f}s · "
          f"总耗时 {sum(r['总耗时'] for r in rows) / n:.2f}s · "
          f"token 事件 {sum(r['token 事件'] for r in rows) / n:.0f} 次")
    print("\n  说明：首 token（TTFT）是用户在页面最直观的等待感来源；"
          "token 事件数越少，前端渲染压力越小。\n")


if __name__ == "__main__":
    main()
