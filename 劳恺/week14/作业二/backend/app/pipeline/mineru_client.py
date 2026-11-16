"""MinerU 解析适配器 —— 真实实现 + Mock。

对应文档：``docs/05-MinerU接入.md``。

为什么要有这一层：

* MinerU 官方 v4 API 有**两种提交模式**（``upload`` 两段式签名 PUT / ``url``），
  响应结构不同；适配器内部统一成 :class:`TaskState`，上层 Worker 只认 ``TaskState``。
* 字段级定义官方未公开（实测结论见 ``docs/05`` §5），解析代码必须**字段容错**。

**实测踩到的两个坑（2026-11-12）**

1. ``data_id`` 只允许 ``[A-Za-z0-9_.-]`` 且 ≤128 字符，**中文文件名不能直接用**。
2. 签名 PUT **绝不能带 Content-Type**。把 ``Content-Type`` 设在 client 级 headers 上
   会导致 OSS 返回 **403 Forbidden** —— 上传必须用独立 client。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# MinerU 任务状态（官方 state 取值）
STATE_PENDING = "pending"
STATE_RUNNING = "running"
STATE_CONVERTING = "converting"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_WAITING_FILE = "waiting-file"

TERMINAL_STATES = frozenset({STATE_DONE, STATE_FAILED})

#: 官方错误码 → 用户可读的中文说明（``docs/05`` §6）
ERROR_MESSAGES: dict[str, str] = {
    "A0202": "MinerU Token 错误，请检查 Authorization 是否为 Bearer <token>",
    "A0211": "MinerU Token 已过期，请到控制台重新申请",
    "-500": "MinerU 传参错误，请检查解析参数",
    "-60005": "文件超过 200MB，MinerU 无法解析",
    "-60006": "页数超过 200 页，请用 page_ranges 截取或拆分文件",
    "-60008": "MinerU 读取文件超时（URL 不可达），请改用上传模式",
    "-60009": "MinerU 提交队列已满，请稍后重试",
    "-60018": "MinerU 今日任务数已达上限，请次日再试",
    "-60019": "MinerU HTML 解析额度不足",
}

_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


class MineruError(RuntimeError):
    """MinerU 调用失败。消息对用户可读（中文）。"""


def make_data_id(doc_id: str) -> str:
    """把 ``doc_id`` 转成 MinerU 允许的 ``data_id``。

    官方限制：≤128 字符，字符集 ``[A-Za-z0-9_.-]``。``doc_id`` 本身形如
    ``d_ab12cd34ef56``，天然合法；这里只做兜底清洗。
    """
    cleaned = _SAFE_ID_RE.sub("-", doc_id)[:128]
    return cleaned or "doc"


def translate_error(code: str | int | None, raw: str = "") -> str:
    """把 MinerU 错误码转成可读中文。未知错误码带上原始信息。"""
    key = str(code) if code is not None else ""
    if key in ERROR_MESSAGES:
        return f"{ERROR_MESSAGES[key]}（code={key}）"
    if raw:
        return f"MinerU 解析失败: {raw}"
    return f"MinerU 调用失败（code={key or '未知'}）"


def map_task_state(data: dict[str, Any]) -> TaskState:
    """把官方轮询响应（单任务或批量的一项）映射成统一的 :class:`TaskState`。

    ``extract_progress`` 只在 ``running`` 阶段有效，可映射到进度条。
    """
    state = str(data.get("state") or "").strip()
    progress = 0.0
    raw_progress = data.get("extract_progress") or {}
    extracted = raw_progress.get("extracted_pages")
    total = raw_progress.get("total_pages")
    if isinstance(extracted, int) and isinstance(total, int) and total > 0:
        progress = min(1.0, extracted / total)
    if state == STATE_DONE:
        progress = 1.0

    error = None
    if state == STATE_FAILED:
        error = translate_error(data.get("err_code"), str(data.get("err_msg") or ""))

    return TaskState(
        state=state or STATE_PENDING,
        progress=progress,
        zip_url=data.get("full_zip_url") or None,
        error=error,
    )


@dataclass
class TaskState:
    """两种提交模式统一后的任务状态。"""

    state: str
    progress: float = 0.0
    zip_url: str | None = None
    error: str | None = None


@dataclass
class MineruResult:
    """一次解析的产物。"""

    task_id: str
    markdown: str = ""
    content_list: list[dict] = field(default_factory=list)
    image_files: list[Path] = field(default_factory=list)
    work_dir: Path | None = None


@runtime_checkable
class MineruClient(Protocol):
    """解析适配器接口。"""

    async def parse_pdf(self, pdf_path: Path, doc_id: str = "") -> MineruResult:
        """提交任务、轮询、解压，返回解析产物。失败时抛带中文原因的异常。"""
        ...

    async def health(self) -> bool:
        """连通性自检。"""
        ...


# ---------------------------------------------------------------------------
# 解压与产物定位（纯函数，便于单测）
# ---------------------------------------------------------------------------


def safe_extract(zip_path: Path, dest: Path) -> list[Path]:
    """解压 zip 并**防 Zip Slip**：逐成员校验 resolve 后仍在目标目录内。

    返回实际解压出的文件路径列表。
    """
    dest_resolved = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (dest_resolved / member.filename).resolve()
            if not target.is_relative_to(dest_resolved):
                logger.warning("跳过路径穿越成员: %s", member.filename)
                continue
            archive.extract(member, dest_resolved)
            if not member.is_dir():
                extracted.append(target)
    return extracted


def find_content_list(extract_dir: Path) -> Path:
    """在解压目录里找 ``*_content_list.json``（**排除** ``*_content_list_v2.json``）。"""
    candidates = [
        path
        for path in extract_dir.rglob("*_content_list.json")
        if not path.name.endswith("_content_list_v2.json")
    ]
    if not candidates:
        raise MineruError("MinerU 产物中没有 content_list.json，无法切块")
    return sorted(candidates)[0]


def find_markdown(extract_dir: Path) -> str:
    markdown = extract_dir / "full.md"
    return markdown.read_text(encoding="utf-8") if markdown.is_file() else ""


def find_images(extract_dir: Path) -> list[Path]:
    images_dir = extract_dir / "images"
    if not images_dir.is_dir():
        return []
    return sorted(path for path in images_dir.iterdir() if path.is_file())


# ---------------------------------------------------------------------------
# 真实实现
# ---------------------------------------------------------------------------


class MineruApiClient:
    """MinerU v4 精准解析 API 适配器（``upload`` / ``url`` 双模式）。"""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.mineru_base_url,
            headers={"Authorization": f"Bearer {settings.mineru_api_key}"},
            timeout=httpx.Timeout(120.0, connect=15.0),
        )

    # ---------- 对外 ----------

    async def parse_pdf(self, pdf_path: Path, doc_id: str = "") -> MineruResult:
        """完整流程：提交 → 轮询 → 下载 → 解压 → 定位产物。"""
        data_id = make_data_id(doc_id or pdf_path.stem)
        work_root = self._settings.work_dir / data_id
        work_root.mkdir(parents=True, exist_ok=True)

        if self._settings.mineru_submit_mode == "url":
            raise MineruError(
                "url 模式需要公网可达的 PDF 地址；本地文件请用 upload 模式"
            )
        task_key = await self._submit_upload(pdf_path, data_id)
        state = await self._poll_batch(task_key)
        return await self._finish(state, task_key, work_root)

    async def parse_url(self, url: str, doc_id: str) -> MineruResult:
        """URL 模式：文件已经在公网可达的地址上。"""
        data_id = make_data_id(doc_id)
        work_root = self._settings.work_dir / data_id
        work_root.mkdir(parents=True, exist_ok=True)

        task_id = await self._submit_url(url, data_id)
        state = await self._poll_task(task_id)
        return await self._finish(state, task_id, work_root)

    async def health(self) -> bool:
        try:
            response = await self._client.get("/v4/extract/task/healthcheck", timeout=5.0)
            # 只要不是 5xx 就认为服务可达（该端点可能不存在，返回 404 也算可达）
            return response.status_code < 500
        except Exception:  # noqa: BLE001 —— 健康检查不应因任何异常抛出
            logger.warning("MinerU 探测失败", exc_info=True)
            return False

    async def close(self) -> None:
        await self._client.close()

    # ---------- 提交 ----------

    def _parse_params(self, data_id: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model_version": self._settings.mineru_model_version,
            "is_ocr": self._settings.mineru_is_ocr,
            "enable_table": self._settings.mineru_enable_table,
            "enable_formula": self._settings.mineru_enable_formula,
            "language": self._settings.mineru_language,
            # 必须为 true：默认 15 分钟 URL 缓存，否则重解析拿到旧结果
            "no_cache": True,
            "extra_formats": [],
            "data_id": data_id,
        }
        if self._settings.mineru_page_ranges:
            params["page_ranges"] = self._settings.mineru_page_ranges
        return params

    async def _submit_upload(self, pdf_path: Path, data_id: str) -> str:
        """模式 B：申请签名链接 → PUT 原始字节。返回 ``batch_id``。"""
        response = await self._client.post(
            self._settings.mineru_file_urls_path,
            json={
                "files": [{"name": pdf_path.name, "data_id": data_id}],
                **self._parse_params(data_id),
            },
        )
        self._raise_for_api_error(response, "申请上传链接失败")
        payload = response.json()
        data = payload.get("data") or {}
        file_urls = data.get("file_urls") or []
        batch_id = data.get("batch_id")
        if not file_urls or not batch_id:
            raise MineruError(f"MinerU 未返回上传链接: {payload}")

        # 独立 client：绝不能把 Authorization / Content-Type 带到签名 PUT 上（否则 403）
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as oss:
            put_response = await oss.put(file_urls[0], content=pdf_path.read_bytes())
            if put_response.status_code >= 400:
                raise MineruError(
                    f"上传文件到 MinerU OSS 失败（HTTP {put_response.status_code}）。"
                    "常见原因：请求带了 Content-Type 请求头"
                )
        logger.info("MinerU 上传完成 batch_id=%s doc=%s", batch_id, data_id)
        return batch_id

    async def _submit_url(self, url: str, data_id: str) -> str:
        """模式 A：URL 建任务。返回 ``task_id``。"""
        response = await self._client.post(
            self._settings.mineru_task_path,
            json={"url": url, **self._parse_params(data_id)},
        )
        self._raise_for_api_error(response, "创建解析任务失败")
        task_id = (response.json().get("data") or {}).get("task_id")
        if not task_id:
            raise MineruError("MinerU 未返回 task_id")
        return str(task_id)

    # ---------- 轮询 ----------

    async def _poll_batch(self, batch_id: str) -> TaskState:
        path = self._settings.mineru_batch_result_path.format(batch_id=batch_id)
        return await self._poll(path, is_batch=True)

    async def _poll_task(self, task_id: str) -> TaskState:
        path = self._settings.mineru_result_path.format(task_id=task_id)
        return await self._poll(path, is_batch=False)

    async def _poll(self, path: str, *, is_batch: bool) -> TaskState:
        elapsed = 0.0
        interval = self._settings.mineru_poll_interval
        timeout = self._settings.mineru_poll_timeout
        last = TaskState(state=STATE_PENDING)

        while elapsed < timeout:
            response = await self._client.get(path)
            self._raise_for_api_error(response, "查询解析结果失败")
            payload = response.json()
            data = payload.get("data") or {}

            if is_batch:
                results = data.get("extract_result") or []
                if not results:
                    state = TaskState(state=STATE_WAITING_FILE)
                else:
                    state = map_task_state(results[0])
            else:
                state = map_task_state(data)

            if state.state != last.state:
                logger.info("MinerU 状态 %s → %s (%.0fs)", last.state, state.state, elapsed)
                last = state

            if state.state == STATE_DONE:
                if not state.zip_url:
                    raise MineruError("MinerU 任务完成但没有返回 zip 地址")
                return state
            if state.state == STATE_FAILED:
                raise MineruError(state.error or "MinerU 解析失败")

            await asyncio.sleep(interval)
            elapsed += interval

        raise MineruError(
            f"MinerU 解析超时（{timeout:.0f} 秒）。可用 page_ranges 减少页数后重试"
        )

    # ---------- 收尾 ----------

    async def _finish(self, state: TaskState, task_id: str, work_root: Path) -> MineruResult:
        if not state.zip_url:
            raise MineruError("MinerU 任务完成但没有返回 zip 地址")
        extract_dir = work_root / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        zip_path = work_root / "result.zip"
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0)) as client:
            response = await client.get(state.zip_url)
            response.raise_for_status()
        zip_path.write_bytes(response.content)

        safe_extract(zip_path, extract_dir)

        content_path = find_content_list(extract_dir)
        content_list = json.loads(content_path.read_text(encoding="utf-8"))

        return MineruResult(
            task_id=task_id,
            markdown=find_markdown(extract_dir),
            content_list=content_list,
            image_files=find_images(extract_dir),
            work_dir=extract_dir,
        )

    @staticmethod
    def _raise_for_api_error(response: httpx.Response, context: str) -> None:
        if response.status_code == 429:
            raise MineruError("MinerU 触发频控（50 文件/分钟），请稍后重试")
        if response.status_code >= 400:
            raise MineruError(f"{context}（HTTP {response.status_code}）")
        try:
            payload = response.json()
        except ValueError:
            return
        code = payload.get("code")
        if code not in (0, None):
            raise MineruError(translate_error(code, str(payload.get("msg") or "")))


# ---------------------------------------------------------------------------
# Mock 实现
# ---------------------------------------------------------------------------

#: 供离线开发的样例 content_list，覆盖实测到的类型 + docs 假设的类型
MOCK_CONTENT_LIST: list[dict] = [
    {
        "type": "text",
        "text": "## 1. 引言\n本文研究多模态检索增强生成在中文文档场景下的表现。",
        "text_level": 1,
        "page_idx": 0,
        "bbox": [72.0, 100.2, 523.8, 180.4],
    },
    {
        "type": "text",
        "text": "实验在三个公开数据集上进行，评价指标为 Hit@8 与 MRR。",
        "page_idx": 0,
        "bbox": [72.0, 190.0, 523.8, 240.0],
    },
    {
        "type": "table",
        "table_body": "<table><tr><td>季度</td><td>营收</td></tr>"
        "<tr><td>2024Q3</td><td>42.3</td></tr></table>",
        "table_caption": ["表 1 分季度营收"],
        "table_footnote": [],
        "img_path": "images/mock_table.png",
        "page_idx": 1,
        "bbox": [70.0, 200.0, 525.0, 400.0],
    },
    {
        "type": "equation",
        "text": "E = mc^2",
        "page_idx": 2,
    },
    {
        "type": "footer",
        "text": "• 第 3 页",
        "page_idx": 2,
    },
]


class MockMineruClient:
    """桩实现：返回固定的 :data:`MOCK_CONTENT_LIST`，不产生任何外网请求。"""

    def __init__(self, work_dir: Path | None = None) -> None:
        self._work_dir = work_dir

    async def parse_pdf(self, pdf_path: Path, doc_id: str = "") -> MineruResult:
        return MineruResult(
            task_id="t_mock00000000",
            markdown="# Mock 解析结果\n\n（未调用真实 MinerU）",
            content_list=[dict(item) for item in MOCK_CONTENT_LIST],
            image_files=[],
            work_dir=self._work_dir,
        )

    async def health(self) -> bool:
        return True


def build_mineru_client(settings: Settings) -> MineruClient:
    """按 ``MOCK_MODE``（可被 ``MOCK_MINERU`` 单独覆盖）选择实现。"""
    if settings.use_mock_mineru:
        return MockMineruClient(work_dir=settings.work_dir)
    return MineruApiClient(settings)
