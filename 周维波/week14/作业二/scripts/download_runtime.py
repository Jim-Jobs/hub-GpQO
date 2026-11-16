"""下载本地运行时：便携 JDK 17 + Kafka 3.9（KRaft 单机，无需 Docker/ZooKeeper）。

用法：
    python scripts/download_runtime.py            # 缺什么补什么
    python scripts/download_runtime.py --force    # 重新下载

下载源可用环境变量覆盖（国内网络建议自备镜像）：
    JDK_URL   默认 https://download.java.net/.../openjdk-17.0.2_windows-x64_bin.zip
    KAFKA_URL 默认 https://archive.apache.org/dist/kafka/3.9.1/kafka_2.13-3.9.1.tgz
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / ".runtime"

JDK_URL = os.getenv(
    "JDK_URL",
    "https://repo.huaweicloud.com/openjdk/17.0.2/openjdk-17.0.2_windows-x64_bin.zip",
)
KAFKA_URL = os.getenv(
    "KAFKA_URL",
    "https://repo.huaweicloud.com/apache/kafka/3.9.1/kafka_2.13-3.9.1.tgz",
)


def _download(url: str, target: Path) -> None:
    if target.exists() and target.stat().st_size > 1024 * 1024:
        print(f"已存在，跳过下载：{target}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    print(f"下载 {url}\n  -> {target}")
    started = time.time()
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response, tmp.open("wb") as sink:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        last_report = 0.0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            sink.write(chunk)
            downloaded += len(chunk)
            now = time.time()
            if now - last_report > 2:
                last_report = now
                percent = f"{downloaded / total * 100:5.1f}%" if total else "?"
                print(f"  {percent}  {downloaded / 1048576:.1f} MB", flush=True)
    tmp.replace(target)
    print(f"完成：{target}（{target.stat().st_size / 1048576:.1f} MB，{time.time() - started:.0f}s）")


def _extract_zip(archive: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        print(f"已解压，跳过：{destination}")
        return
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(destination)


def _extract_tgz(archive: Path, destination: Path) -> None:
    if destination.exists() and any(destination.iterdir()):
        print(f"已解压，跳过：{destination}")
        return
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as bundle:
        bundle.extractall(destination, filter="data")


def ensure_runtime(force: bool = False) -> tuple[Path, Path]:
    RUNTIME.mkdir(parents=True, exist_ok=True)

    jdk_zip = RUNTIME / "jdk.zip"
    jdk_dir = RUNTIME / "jdk"
    if force:
        jdk_zip.unlink(missing_ok=True)
        shutil.rmtree(jdk_dir, ignore_errors=True)
    _download(JDK_URL, jdk_zip)
    _extract_zip(jdk_zip, jdk_dir)
    java_home = next(
        (path.parent.parent for path in jdk_dir.glob("*/bin/java.exe")), None
    ) or (jdk_dir if (jdk_dir / "bin" / "java.exe").exists() else None)
    if java_home is None:
        raise RuntimeError(f"JDK 解压后未找到 bin/java.exe：{jdk_dir}")

    kafka_tgz = RUNTIME / "kafka.tgz"
    kafka_dir = RUNTIME / "kafka"
    if force:
        kafka_tgz.unlink(missing_ok=True)
        shutil.rmtree(kafka_dir, ignore_errors=True)
    _download(KAFKA_URL, kafka_tgz)
    _extract_tgz(kafka_tgz, kafka_dir)
    kafka_home = next(
        (path.parent.parent.parent for path in kafka_dir.glob("*/bin/windows/kafka-server-start.bat")),
        None,
    )
    if kafka_home is None:
        raise RuntimeError(f"Kafka 解压后未找到 bin/windows/kafka-server-start.bat：{kafka_dir}")

    print(f"\nJAVA_HOME = {java_home}")
    print(f"KAFKA_HOME = {kafka_home}")
    return java_home, kafka_home


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="下载本地 JDK + Kafka")
    parser.add_argument("--force", action="store_true", help="重新下载并解压")
    args = parser.parse_args()
    try:
        ensure_runtime(force=args.force)
    except Exception as exc:
        print(f"下载失败：{exc}", file=sys.stderr)
        sys.exit(1)
