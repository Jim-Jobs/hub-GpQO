"""探针脚本：跑一次真实 MinerU 任务，打印 ``content_list`` 的字段与类型分布。

**为什么必须先跑这个**（``docs/05`` §5、``docs/09`` §3）：官方 API 文档没有公开
``content_list.json`` 的字段级定义，切块代码完全依赖它。不先实测就写切块逻辑，
大概率返工。

用法（Windows Git Bash）::

    cd backend
    MINERU_API_KEY=<你的token> PYTHONIOENCODING=utf-8 \\
        "C:/Users/80627/anaconda3/envs/py312/python.exe" scripts/probe_mineru.py /path/to/demo.pdf

    # 或用公网 URL 走 url 模式
    ... scripts/probe_mineru.py --url https://example.com/demo.pdf

本脚本**只打印观察结果**，不做任何解析入库，产出物落在临时目录。
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import httpx

from app.config import Settings

# 官方 content_list 元素的字段名在不同版本间可能有出入，这里只作提示，
# 真正要靠本脚本打印出来的实际字段集合来定稿。
KNOWN_FIELDS_HINT = {
    "text": ["type", "text", "text_format", "text_level", "page_idx", "bbox"],
    "table": ["type", "table_body", "table_caption", "page_idx", "bbox"],
    "image": ["type", "img_path", "image_caption", "image_footnote", "page_idx", "bbox"],
    "equation": ["type", "text", "text_format", "page_idx"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="探测 MinerU content_list 的真实结构")
    parser.add_argument("pdf", nargs="?", help="本地 PDF 路径（upload 模式）")
    parser.add_argument("--url", help="公网可达的 PDF URL（url 模式）")
    parser.add_argument("--timeout", type=float, default=1800.0, help="轮询超时秒数")
    return parser.parse_args()


def build_parse_params(settings: Settings, data_id: str) -> dict:
    """解析参数（docs/05 §6 的多模态 RAG 推荐配置）。"""
    params: dict = {
        "model_version": settings.mineru_model_version,
        "is_ocr": settings.mineru_is_ocr,
        "enable_table": settings.mineru_enable_table,
        "enable_formula": settings.mineru_enable_formula,
        "language": settings.mineru_language,
        # 必须为 true：默认 15 分钟 URL 缓存，否则重解析拿到旧结果
        "no_cache": settings.mineru_no_cache,
        "extra_formats": [],
        "data_id": data_id,
    }
    if settings.mineru_page_ranges:
        params["page_ranges"] = settings.mineru_page_ranges
    return params


async def submit_upload(
    client: httpx.AsyncClient, settings: Settings, pdf_path: Path
) -> str:
    """模式 B：申请签名链接 → PUT 原始字节到 OSS。返回 batch_id。"""
    # data_id 必须是 ≤128 字符且只含 [A-Za-z0-9_.-]，中文文件名不能直接用
    data_id = "probe-" + hashlib.sha256(pdf_path.name.encode("utf-8")).hexdigest()[:12]
    body = {
        "files": [{"name": pdf_path.name, "data_id": data_id}],
        **build_parse_params(settings, data_id),
    }
    url = f"{settings.mineru_base_url}{settings.mineru_file_urls_path}"
    response = await client.post(url, json=body)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"申请上传链接失败: {payload}")
    data = payload["data"]
    file_url = data["file_urls"][0]
    batch_id = data["batch_id"]

    print(f"[upload] 拿到签名链接，PUT {pdf_path.name}（{pdf_path.stat().st_size} 字节）")
    # 官方要求：PUT 原始字节，且**不要设置 Content-Type 请求头**。
    # 这里必须用独立 client：主 client 上有 Authorization / Content-Type，
    # 任何一个被带上去都会让 OSS 签名校验失败（403）。
    async with httpx.AsyncClient(timeout=300.0) as oss_client:
        put_response = await oss_client.put(file_url, content=pdf_path.read_bytes())
        put_response.raise_for_status()
    print("[upload] 上传完成，系统会自动扫描并提交解析任务")
    return batch_id


async def submit_url(client: httpx.AsyncClient, settings: Settings, url: str) -> str:
    """模式 A：URL 方式建任务。返回 task_id。"""
    body = {"url": url, **build_parse_params(settings, "probe-url")}
    endpoint = f"{settings.mineru_base_url}{settings.mineru_task_path}"
    response = await client.post(endpoint, json=body)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"创建任务失败: {payload}")
    task_id = payload["data"]["task_id"]
    print(f"[url] 任务已创建: {task_id}")
    return task_id


async def poll_upload(
    client: httpx.AsyncClient, settings: Settings, batch_id: str, timeout: float
) -> str:
    path = settings.mineru_batch_result_path.format(batch_id=batch_id)
    return await _poll(client, settings, path, timeout, is_batch=True)


async def poll_task(
    client: httpx.AsyncClient, settings: Settings, task_id: str, timeout: float
) -> str:
    path = settings.mineru_result_path.format(task_id=task_id)
    return await _poll(client, settings, path, timeout, is_batch=False)


async def _poll(
    client: httpx.AsyncClient,
    settings: Settings,
    path: str,
    timeout: float,
    *,
    is_batch: bool,
) -> str:
    """轮询直到拿到 zip 地址。"""
    elapsed = 0.0
    while elapsed < timeout:
        response = await client.get(f"{settings.mineru_base_url}{path}")
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"查询失败: {payload}")

        data = payload["data"]
        if is_batch:
            results = data.get("extract_result") or []
            if not results:
                await asyncio.sleep(settings.mineru_poll_interval)
                elapsed += settings.mineru_poll_interval
                continue
            item = results[0]
            state = item.get("state")
            progress = item.get("extract_progress") or {}
            zip_url = item.get("full_zip_url")
            err = item.get("err_msg")
        else:
            state = data.get("state")
            progress = data.get("extract_progress") or {}
            zip_url = data.get("full_zip_url")
            err = data.get("err_msg")

        print(f"  [{elapsed:6.0f}s] state={state} progress={progress}")
        if state == "done":
            if not zip_url:
                raise RuntimeError("任务已完成但没有 full_zip_url")
            return zip_url
        if state == "failed":
            raise RuntimeError(f"解析失败: {err}")
        await asyncio.sleep(settings.mineru_poll_interval)
        elapsed += settings.mineru_poll_interval
    raise TimeoutError(f"轮询超时（{timeout}s）")


def safe_extract(zip_path: Path, dest: Path) -> None:
    """解压并防 Zip Slip（docs/05 §8.5）。"""
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest / member.filename).resolve()
            if not target.is_relative_to(dest):
                print(f"  [跳过] 路径穿越成员: {member.filename}")
                continue
            archive.extract(member, dest)


def report(work_dir: Path) -> None:
    """打印 content_list 的字段集合、type 分布与样例。"""
    candidates = sorted(work_dir.rglob("*_content_list.json"))
    if not candidates:
        print("!! zip 中没有找到 *_content_list.json，产物结构与预期不符")
        for item in sorted(work_dir.rglob("*")):
            print("   ", item.relative_to(work_dir))
        return

    content_list = json.loads(candidates[0].read_text(encoding="utf-8"))
    print("\n" + "=" * 70)
    print(f"content_list 文件: {candidates[0].relative_to(work_dir)}")
    print(f"元素总数: {len(content_list)}")

    fields = sorted({key for element in content_list for key in element})
    print(f"\n【字段集合】（{len(fields)} 个）\n  {fields}")

    types = collections.Counter(element.get("type") for element in content_list)
    print(f"\n【type 分布】\n  {dict(types)}")

    print("\n【每种 type 的首个样例】")
    seen: set[str | None] = set()
    for element in content_list:
        kind = element.get("type")
        if kind in seen:
            continue
        seen.add(kind)
        print(f"\n--- type={kind} ---")
        print(json.dumps(element, ensure_ascii=False, indent=2)[:1200])

    print("\n【与已知字段口径的差异】")
    for kind, expected in KNOWN_FIELDS_HINT.items():
        actual = sorted({k for e in content_list if e.get("type") == kind for k in e})
        if not actual:
            continue
        missing = [f for f in expected if f not in actual]
        extra = [f for f in actual if f not in expected]
        flag = "✅" if not missing and not extra else "⚠️"
        print(f"  {flag} {kind}: 缺失={missing or '无'} 多出={extra or '无'}")

    print("\n【表格字段是 HTML 还是 Markdown】")
    for element in content_list:
        if element.get("type") == "table":
            body = element.get("table_body") or element.get("text") or ""
            print(f"  开头 200 字符: {body[:200]!r}")
            break

    print("\n【图片：img_path 与实际文件是否对得上】")
    img_paths = [e.get("img_path") for e in content_list if e.get("type") == "image"]
    img_paths = [p for p in img_paths if p]
    if not img_paths:
        print("  （本次解析没有图片）")
    for img_path in img_paths[:5]:
        matches = list(work_dir.rglob(Path(img_path).name))
        status = "✅ 找到" if matches else "❌ 未找到"
        print(f"  {status} {img_path}")
    image_dirs = [d for d in work_dir.rglob("images") if d.is_dir()]
    for image_dir in image_dirs:
        files = sorted(p.name for p in image_dir.iterdir() if p.is_file())
        print(f"  images/ 目录共 {len(files)} 个文件，前 5 个: {files[:5]}")

    print("\n【产出物清单】")
    for item in sorted(work_dir.rglob("*")):
        if item.is_file():
            print(f"  {item.relative_to(work_dir)}  ({item.stat().st_size} 字节)")


async def main() -> int:
    args = parse_args()
    settings = Settings()

    if not settings.mineru_api_key:
        print(
            "✗ MINERU_API_KEY 为空。请先申请 token：https://mineru.net/apiManage/token",
            file=sys.stderr,
        )
        return 2
    if not args.pdf and not args.url:
        print("✗ 需要提供本地 PDF 路径或 --url", file=sys.stderr)
        return 2

    # 不带 Content-Type：httpx 会在 json= 时自动补 application/json，
    # 而它绝不会被带到签名 PUT 上（那条链路用独立 client）
    headers = {"Authorization": f"Bearer {settings.mineru_api_key}"}
    work_dir = settings.work_dir / "probe"
    work_dir.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(headers=headers, timeout=60.0) as client:
        if args.url:
            print(f"模式：url（{args.url}）")
            task_id = await submit_url(client, settings, args.url)
            zip_url = await poll_task(client, settings, task_id, args.timeout)
        else:
            pdf_path = Path(args.pdf).resolve()
            if not pdf_path.is_file():
                print(f"✗ 文件不存在: {pdf_path}", file=sys.stderr)
                return 2
            print(f"模式：upload（{pdf_path}）")
            batch_id = await submit_upload(client, settings, pdf_path)
            zip_url = await poll_upload(client, settings, batch_id, args.timeout)

    print(f"\n下载 zip: {zip_url}")
    zip_path = work_dir / "result.zip"
    async with httpx.AsyncClient(timeout=300.0) as client:
        response = await client.get(zip_url)
        response.raise_for_status()
    zip_path.write_bytes(response.content)
    print(f"zip 大小: {zip_path.stat().st_size} 字节")

    # 每次跑用独立目录：否则上一次的产物会混进本次的清单与 content_list 检索
    extract_dir = work_dir / f"extracted_{zip_path.stem}_{len(list(work_dir.glob('extracted_*')))}"
    safe_extract(zip_path, extract_dir)
    report(extract_dir)
    print(f"\n产物目录: {extract_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
