"""调用 MinerU Tasks API 并把结果安全地保存到文档目录。"""

from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from zipfile import BadZipFile, ZipFile

from backend.app.core.config import ConfigurationError, Settings
from backend.app.documents.application.process_document import (
    PermanentProcessingError,
    RetryableProcessingError,
)
from backend.app.documents.domain.models import IngestEvent, ParsedDocument
from backend.app.documents.parsing.asset_registry import register_assets
from backend.app.documents.parsing.result_reader import MineruOutputError, read_result
from backend.app.integrations.mineru_client import MineruApiError, MineruTasksClient


def parse_document(
    event: IngestEvent, settings: Settings, *, client: MineruTasksClient | None = None
) -> ParsedDocument:
    """解析单份 PDF，将 ZIP 产物归一化后放在 data/parsed/{document_id}/。"""
    if not settings.mineru_base_url:
        raise ConfigurationError("缺少必要服务配置：RAG_MINERU_BASE_URL")
    source_path, output_dir = _document_paths(event, settings)
    settings.initialize_data_directories()
    output_dir.mkdir(parents=True, exist_ok=True)
    tasks_client = client or MineruTasksClient(
        settings.mineru_base_url,
        poll_interval_seconds=settings.mineru_poll_interval_seconds,
    )

    try:
        result_zip = tasks_client.parse_pdf(
            source_path, timeout_seconds=settings.mineru_task_timeout_seconds
        )
        _extract_zip(result_zip, output_dir)
        assets = register_assets(event.document_id, output_dir)
        return read_result(event.document_id, output_dir, assets)
    except MineruApiError as error:
        if error.retryable:
            raise RetryableProcessingError(f"MinerU 暂时无法接单：{error}") from error
        # 模糊提交、任务失败或结果读取失败不会触发外层重复创建任务。
        raise PermanentProcessingError(f"MinerU 文档解析失败：{error}") from error
    except (MineruOutputError, BadZipFile, OSError, ValueError) as error:
        # 外层 Kafka 生命周期会重跑整个处理函数；MinerU 新建任务没有幂等键，
        # 所以这里终止本条任务，避免一次模糊结果自动创建重复解析任务。
        raise PermanentProcessingError(f"MinerU 文档解析失败：{error}") from error


def _document_paths(event: IngestEvent, settings: Settings) -> tuple[Path, Path]:
    """校验 Kafka 相对路径和文档 ID，确保读写不越过各自数据目录。"""
    relative_path = PurePosixPath(event.relative_path)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise PermanentProcessingError("Kafka 文档路径不是安全的相对路径")
    source_path = (settings.data_root / Path(*relative_path.parts)).resolve()
    uploads_root = settings.uploads_dir.resolve()
    if not source_path.is_relative_to(uploads_root) or not source_path.is_file():
        raise PermanentProcessingError("Kafka 文档路径不存在或超出 uploads 目录")
    invalid_document_id = (
        not event.document_id
        or event.document_id in {".", ".."}
        or "/" in event.document_id
        or "\\" in event.document_id
        or "\x00" in event.document_id
        or bool(PureWindowsPath(event.document_id).drive)
    )
    if invalid_document_id:
        raise PermanentProcessingError("Kafka 文档 ID 不是有效目录名")
    output_candidate = settings.parsed_dir / event.document_id
    parsed_root = settings.parsed_dir.resolve()
    if output_candidate.is_symlink():
        raise PermanentProcessingError("文档解析目录不能是符号链接")
    output_dir = output_candidate.resolve()
    if output_dir.parent != parsed_root:
        raise PermanentProcessingError("文档解析目录超出 parsed 根目录")
    return source_path, output_dir


def _extract_zip(result_zip: bytes, output_dir: Path) -> None:
    """逐成员安全解压 ZIP，拒绝绝对路径、路径穿越和符号链接。"""
    try:
        with ZipFile(BytesIO(result_zip)) as archive:
            for member in archive.infolist():
                member_path = PurePosixPath(member.filename)
                if (
                    not member.filename
                    or member_path.is_absolute()
                    or PureWindowsPath(member.filename).drive
                    or "\\" in member.filename
                    or ".." in member_path.parts
                ):
                    raise MineruOutputError("MinerU ZIP 包含越界路径")
                target = (output_dir / Path(*member_path.parts)).resolve()
                if not target.is_relative_to(output_dir.resolve()):
                    raise MineruOutputError("MinerU ZIP 成员超出文档解析目录")
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                # ZIP 外部属性的高位表示 Unix 文件类型；不接受符号链接条目。
                file_type = (member.external_attr >> 16) & 0o170000
                if file_type == 0o120000:
                    raise MineruOutputError("MinerU ZIP 不允许包含符号链接")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
    except BadZipFile:
        raise
