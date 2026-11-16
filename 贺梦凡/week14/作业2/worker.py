"""消费 pdf.parse，调用 MinerU 解析 PDF，并把文本和图片写入 Redis。"""

import json
import os
import shutil
import signal
import subprocess
import zipfile
from pathlib import Path

from confluent_kafka import Consumer, KafkaError

from common import (
    TOPIC_PARSE,
    KAFKA_BOOTSTRAP,
    chunks_key,
    connect_redis,
    doc_key,
    ensure_topic,
    replace_chunks,
    set_status,
)
from indexlib import chunks_from_content_list, chunks_from_markdown, load_content_list

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("RAG_DATA", ROOT / "data")).resolve()
_running = True


def _stop(signum, _frame) -> None:
    global _running
    _running = False
    print(f"收到信号 {signum}，解析进程准备退出", flush=True)


def mineru_command(pdf: Path, out: Path) -> list[str]:
    mineru = shutil.which("mineru")
    if mineru:
        probe = subprocess.run([mineru, "--help"], capture_output=True, text=True)
        blob = f"{probe.stdout}\n{probe.stderr}"
        if "-p" in blob and "-o" in blob:
            command = [mineru, "-p", str(pdf), "-o", str(out)]
            device = os.environ.get("MINERU_DEVICE")
            if device:
                command.extend(["-d", device])
            return command
        return [mineru, "parse", str(pdf), "--pages", "all", "-o", str(out / "parsed.md"), "--wait", "900"]
    kit = shutil.which("mineru-kit")
    if kit:
        return [kit, "parse", str(pdf), "-o", str(out), "--format", "zip"]
    raise FileNotFoundError("找不到 mineru 或 mineru-kit，无法解析 PDF")


def unzip_outputs(out: Path) -> None:
    for archive in list(out.rglob("*.zip")):
        dest = archive.with_suffix("")
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as handle:
            handle.extractall(dest)


def collect_chunks(out: Path, doc_id: str, filename: str) -> list[dict]:
    unzip_outputs(out)
    lists = sorted(out.rglob("*content_list*.json"))
    if lists:
        chunks = []
        for path in lists:
            chunks.extend(
                chunks_from_content_list(
                    load_content_list(path),
                    path.parent,
                    DATA_DIR,
                    doc_id,
                    filename,
                )
            )
        if chunks:
            return chunks
    chunks = []
    for path in sorted(out.rglob("*.md")):
        chunks.extend(
            chunks_from_markdown(path.read_text(encoding="utf-8", errors="ignore"), path, DATA_DIR, doc_id, filename)
        )
    return chunks


def run_mineru(pdf: Path, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    command = mineru_command(pdf, out)
    print("执行", " ".join(command), flush=True)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    tail = []
    for line in proc.stdout:
        print(line, end="", flush=True)
        tail.append(line)
        del tail[:-40]
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"MinerU 退出码 {code}\n{''.join(tail)}")


def handle(client, job: dict) -> None:
    doc_id = str(job["doc_id"])
    filename = str(job.get("filename") or Path(job["path"]).name)
    meta = client.hgetall(doc_key(doc_id))
    if meta.get("status") == "done" and client.llen(chunks_key(doc_id)) > 0:
        print(f"已解析过，跳过 {doc_id}", flush=True)
        return
    pdf = Path(job["path"])
    if not pdf.is_file():
        set_status(client, doc_id, "failed", "上传的 PDF 不存在")
        return
    set_status(client, doc_id, "parsing")
    out = DATA_DIR / "parsed" / doc_id
    try:
        run_mineru(pdf, out)
        chunks = collect_chunks(out, doc_id, filename)
    except Exception as exc:
        set_status(client, doc_id, "failed", str(exc)[:800])
        print(f"解析失败 {doc_id}: {exc}", flush=True)
        return
    if not chunks:
        set_status(client, doc_id, "failed", "MinerU 没有产出可检索的文本或图片")
        return
    replace_chunks(client, doc_id, chunks)
    set_status(client, doc_id, "done")
    print(f"解析完成 {filename}，{len(chunks)} 个片段", flush=True)


def main() -> None:
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    ensure_topic()
    client = connect_redis()
    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BOOTSTRAP,
            "group.id": "pdf-parser",
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    consumer.subscribe([TOPIC_PARSE])
    print(f"解析进程已启动，消费 {TOPIC_PARSE}", flush=True)
    try:
        while _running:
            record = consumer.poll(1.0)
            if record is None:
                continue
            if record.error():
                if record.error().code() != KafkaError._PARTITION_EOF:
                    print(f"Kafka 错误: {record.error()}", flush=True)
                continue
            try:
                job = json.loads(record.value().decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                print(f"跳过无法解析的任务: {exc}", flush=True)
                consumer.commit(message=record, asynchronous=False)
                continue
            if "doc_id" not in job or "path" not in job:
                consumer.commit(message=record, asynchronous=False)
                continue
            handle(client, job)
            consumer.commit(message=record, asynchronous=False)
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
