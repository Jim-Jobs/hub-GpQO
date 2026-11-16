"""一键启动：默认用真实 Redis + Kafka；APP_MODE=local 时无需 Docker 也可运行。

    python run.py                 # 同时启动 API 与 Worker（推荐）
    python run.py --mode api      # 只启动网页服务
    python run.py --mode worker   # 只启动后台消费进程（仅 Kafka 模式）
    python run.py --port 8080
    $env:APP_MODE="local"; python run.py     # Windows PowerShell 无 Docker 预览
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Kafka 多模态 RAG 问答系统")
    parser.add_argument("--mode", choices=["all", "api", "worker"], default="all")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    for sub in ("docs", "uploads", "questions", "tmp", "index"):
        (settings.data_dir / sub).mkdir(parents=True, exist_ok=True)

    mode_name = "本地预览（进程内消息总线 + 文件状态，无 Docker）" if settings.local_mode else "Kafka + Redis"
    print(f"[run] 运行模式：{mode_name}")

    if args.mode == "worker":
        if settings.local_mode:
            print("[run] 本地模式的 Worker 已内嵌在网页进程，请直接运行：python run.py")
            return
        raise SystemExit(subprocess.call([sys.executable, "-m", "app.worker"], cwd=str(ROOT)))

    worker_proc = None
    worker_stop = None
    if args.mode == "all":
        if settings.local_mode:
            from app import worker

            worker_stop = threading.Event()
            worker.start_workers(worker_stop)
            print("[run] 本地模式：解析 / 问答 Worker 已在同进程内启动")
        else:
            worker_proc = subprocess.Popen([sys.executable, "-m", "app.worker"], cwd=str(ROOT))
            print(f"[run] Worker PID={worker_proc.pid}，正在消费 Kafka 任务")
    print(f"[run] 网页地址：http://localhost:{args.port}/")

    import uvicorn

    try:
        uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="info")
    finally:
        if worker_stop is not None:
            worker_stop.set()
        if worker_proc is not None and worker_proc.poll() is None:
            worker_proc.terminate()
            try:
                worker_proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                worker_proc.kill()


if __name__ == "__main__":
    main()
