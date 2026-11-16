"""串行运行 ChatAgent、收集测试 trace、评分并生成 JSON 报告。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import mimetypes
from pathlib import Path
import time
from threading import Lock
from typing import Callable, Protocol
from uuid import uuid4

from backend.app.agents.chat.agent import ChatAgent
from backend.app.agents.chat.models import ChatModelClient, ChatVectorStore
from backend.app.agents.chat.state import ChatState
from backend.app.chat.application.task_manager import ChatTaskManager, StageCallback
from backend.app.chat.application.ports import ChatStatusStore
from backend.app.chat.domain.enums import ChatStage, ChatStatus
from backend.app.chat.domain.models import (
    ChatExecutionEvent,
    ChatImage,
    ChatRequest,
    ChatResponse,
    ChatStatusRecord,
)
from backend.app.core.config import ConfigurationError, Settings
from backend.app.documents.domain.models import ChunkSearchHit, DocumentRecord, DocumentStatus
from backend.app.documents.indexing.embed_chunks import TextEmbeddingClient
from backend.app.documents.parsing.asset_registry import load_asset_bytes
from backend.app.integrations.embedding_client import OpenAIEmbeddingClient
from backend.app.integrations.milvus_store import MilvusChunkStore
from backend.app.integrations.redis_document_store import RedisDocumentStore
from backend.app.integrations.vllm_chat import OpenAICompatibleChatClient
from backend.tests.evaluation.agents.scoring_agent import PROMPT_VERSION, ScoringAgent
from backend.tests.evaluation.dataset import EVALUATION_ROOT, GOLDEN_CASES_PATH, load_cases
from backend.tests.evaluation.metrics import compute_metrics, report_passed, scoring_passed, weighted_score
from backend.tests.evaluation.schemas import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationRunReport,
    ScoringResult,
)


TERMINAL_STATUSES = {
    ChatStatus.COMPLETED,
    ChatStatus.NEEDS_CONFIRMATION,
    ChatStatus.CANCELLED,
    ChatStatus.FAILED,
}
POLL_INTERVAL_SECONDS = 0.05


class EvaluationScorer(Protocol):
    """描述黄金答案评分器需要提供的单条评分接口。"""

    def score_answer(
        self,
        case: EvaluationCase,
        response: ChatResponse,
        evidence: list[dict],
        *,
        input_image: ChatImage | None = None,
    ) -> ScoringResult:
        """使用用例标准、实际答案、检索证据和可选用户图生成结构分。"""


class EvaluationChatAgent(Protocol):
    """描述评测 runner 所需的 ChatAgent 执行接口。"""

    def run(
        self, state: ChatState, stage_callback: StageCallback | None = None
    ) -> ChatResponse:
        """执行一次资料库问答，并通过回调报告执行阶段。"""

    def answer_general(
        self, question_text: str, user_image: ChatImage | None
    ) -> ChatResponse:
        """生成确认后的通用回答；黄金集首轮不会确认执行该分支。"""


@dataclass
class _TraceState:
    """保存单个用例在 worker 线程中产生的中间观测值。"""

    case_id: str
    expected_route: str
    expected_behavior: str
    input_image: ChatImage | None
    route: str | None = None
    retrieved_chunks: list[dict] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    citations: list[dict] = field(default_factory=list)
    answer_segments: list[dict] = field(default_factory=list)
    user_image_sent: bool = False
    status_history: list[dict] = field(default_factory=list)
    final_status: str | None = None
    response_route: str | None = None
    chat_id: str | None = None
    error_code: str | None = None
    embedding_calls: int = 0
    vector_search_calls: int = 0

    def to_dict(self) -> dict:
        """导出不含图像二进制的审计数据，供 JSON 报告保存。"""
        return {
            "case_id": self.case_id,
            "chat_id": self.chat_id,
            "expected_route": self.expected_route,
            "expected_behavior": self.expected_behavior,
            "route": self.route,
            "retrieved_chunks": self.retrieved_chunks,
            "stages": self.stages,
            "citations": self.citations,
            "answer_segments": self.answer_segments,
            "user_image_sent": self.user_image_sent,
            "status_history": self.status_history,
            "final_status": self.final_status,
            "response_route": self.response_route,
            "error_code": self.error_code,
            "embedding_calls": self.embedding_calls,
            "vector_search_calls": self.vector_search_calls,
        }


class EvaluationTraceCollector:
    """收集只供评测使用的路由、Top-5、阶段、引用和图像调用信息。"""

    def __init__(self) -> None:
        """初始化当前用例槽和线程锁；trace 不添加到公开 API 响应。"""
        self.current: _TraceState | None = None
        self._lock = Lock()

    def begin_case(self, case: EvaluationCase, input_image: ChatImage | None) -> _TraceState:
        """在执行新用例前清空前一条 trace，并记录本条用户图对象。"""
        with self._lock:
            self.current = _TraceState(
                case.id,
                case.expected_route,
                case.expected_behavior,
                input_image,
            )
            return self.current

    def record_model_call(self, images: list[ChatImage] | None) -> None:
        """判断 ChatAgent 发给 Qwen 的图片中是否包含原始用户图片。"""
        with self._lock:
            trace = self.current
            if trace is None or trace.input_image is None:
                return
            trace.user_image_sent = trace.user_image_sent or any(
                image.data is trace.input_image.data for image in images or []
            )

    def record_embedding_call(self) -> None:
        """记录是否触发了 BGE-M3，以验证范围外问题不做向量化。"""
        with self._lock:
            if self.current is not None:
                self.current.embedding_calls += 1

    def record_vector_search(self) -> None:
        """记录 Milvus 搜索次数，以验证路由分支是否访问向量库。"""
        with self._lock:
            if self.current is not None:
                self.current.vector_search_calls += 1

    def record_stage(self, stage: ChatStage, _retrieved_count: int) -> None:
        """按 ChatAgent 回调顺序记录实际执行阶段。"""
        with self._lock:
            if self.current is not None:
                self.current.stages.append(stage.value)

    def record_state(self, state: ChatState) -> None:
        """从 Agent 测试状态提取实际路由和有序 Top-5 文本证据。"""
        with self._lock:
            if self.current is None:
                return
            self.current.route = (
                state.route_and_query.route if state.route_and_query is not None else None
            )
            self.current.retrieved_chunks = [
                {
                    "chunk_id": item.chunk.chunk_id,
                    "document_id": item.chunk.document_id,
                    "page_number": item.chunk.page_number,
                    "file_name": item.file_name,
                    "text": item.chunk.text,
                    "asset_ids": item.chunk.asset_ids,
                    "score": item.score,
                }
                for item in state.retrieved_chunks[:5]
            ]

    def record_statuses(self, records: list[ChatStatusRecord]) -> None:
        """把内存状态仓库保存的状态快照放入本条用例 trace。"""
        with self._lock:
            if self.current is None:
                return
            self.current.status_history = [
                {"status": record.status.value, "stage": record.stage.value}
                for record in records
            ]

    def record_final(self, record: ChatStatusRecord) -> None:
        """保存最终状态、回答引用和有序文字/图片片段。"""
        with self._lock:
            if self.current is None:
                return
            self.current.final_status = record.status.value
            if record.response is not None:
                self.current.response_route = record.response.route
                self.current.citations = record.response.citations
                self.current.answer_segments = record.response.answer_segments

    def record_error(self, error: Exception) -> None:
        """只记录异常类型名，避免把提示词或用户正文复制进报告。"""
        with self._lock:
            if self.current is not None:
                self.current.error_code = type(error).__name__


class _TracingChatModel:
    """委托真实模型请求，同时记录模型调用是否收到用户输入图片。"""

    def __init__(self, model: ChatModelClient, collector: EvaluationTraceCollector) -> None:
        """保存真实聊天模型和仅用于评测的 trace collector。"""
        self._model = model
        self._collector = collector

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        images: list[ChatImage] | None = None,
    ) -> str:
        """记录图像传递事实后调用原模型，保持线上请求行为不变。"""
        self._collector.record_model_call(images)
        return self._model.complete(system_prompt, user_prompt, images)


class _TracingEmbeddingClient:
    """记录 embedding 请求次数并把调用委托给真实 BGE-M3 adapter。"""

    def __init__(self, client: TextEmbeddingClient, collector: EvaluationTraceCollector) -> None:
        """保存真实向量模型客户端和评测 trace 收集器。"""
        self._client = client
        self._collector = collector

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """记录一次 embedding 调用并保持真实 adapter 的向量输出。"""
        self._collector.record_embedding_call()
        return self._client.embed_texts(texts)


class _TracingVectorStore:
    """记录 Milvus 检索调用并委托给真实向量存储。"""

    def __init__(self, store: ChatVectorStore, collector: EvaluationTraceCollector) -> None:
        """保存 Milvus store 和当前评测的 trace 收集器。"""
        self._store = store
        self._collector = collector

    def search_chunks(
        self,
        embedding: list[float],
        document_ids: list[str],
        limit: int,
    ) -> list[ChunkSearchHit]:
        """记录一次向量搜索后返回真实检索命中。"""
        self._collector.record_vector_search()
        return self._store.search_chunks(embedding, document_ids, limit)


class _MemoryChatStatusStore(ChatStatusStore):
    """在线程安全的内存字典中保存一轮评测的问答状态快照。"""

    def __init__(self) -> None:
        """创建状态表和按 chat_id 分组的历史快照。"""
        self._records: dict[str, ChatStatusRecord] = {}
        self._history: dict[str, list[ChatStatusRecord]] = {}
        self._lock = Lock()

    def save(self, record: ChatStatusRecord) -> None:
        """保存当前问答状态并追加不可变记录供验收。"""
        with self._lock:
            self._records[record.chat_id] = record
            self._history.setdefault(record.chat_id, []).append(record)

    def get(self, chat_id: str) -> ChatStatusRecord | None:
        """读取内存中的当前问答状态。"""
        with self._lock:
            return self._records.get(chat_id)

    def fail_interrupted(self) -> int:
        """新建的单轮内存状态仓库没有重启遗留记录，返回零。"""
        return 0

    def history(self, chat_id: str) -> list[ChatStatusRecord]:
        """返回一个 chat_id 的状态历史副本，避免外部修改内部列表。"""
        with self._lock:
            return list(self._history.get(chat_id, []))


class _NullExecutionLogger:
    """丢弃评测任务协调层日志，避免测试日志混入在线运行日志。"""

    def write(self, _event: ChatExecutionEvent) -> None:
        """评测 trace 已单独保存，因此不额外写入 JSONL 文件。"""

    def close(self) -> None:
        """满足任务管理器关闭接口；该替身没有外部资源。"""


@dataclass(frozen=True)
class EvaluationServices:
    """收集一轮评测所需的 Agent、评分器、目标文档和资源校验器。"""

    settings: Settings
    chat_agent: EvaluationChatAgent
    scoring_agent: EvaluationScorer
    document_id: str
    corpus_profile: str
    fixture_root: Path
    parsed_root: Path
    answer_model: str
    scoring_model: str
    prompt_version: str = PROMPT_VERSION
    image_validator: Callable[[str, str], bool] | None = None


def run_evaluation(
    cases: list[EvaluationCase],
    trace_collector: EvaluationTraceCollector,
    services: EvaluationServices,
    *,
    dataset_sha256: str,
) -> EvaluationRunReport:
    """串行执行黄金用例、评分并汇总可复核的 EvaluationRunReport。"""
    started = datetime.now(timezone.utc)
    status_store = _MemoryChatStatusStore()
    single_worker_settings = replace(
        services.settings,
        chat_max_concurrency=1,
        chat_queue_capacity=1,
    )

    def process_chat(request: ChatRequest, on_stage: StageCallback) -> ChatResponse:
        """为当前用例创建 ChatState，并把 Agent 阶段和终态写入 trace。"""
        user_image = (
            ChatImage(request.image, request.image_media_type or "")
            if request.image is not None
            else None
        )
        state = ChatState(
            request.question_text,
            services.corpus_profile,
            user_image=user_image,
        )

        def record_stage(stage: ChatStage, retrieved_count: int) -> None:
            """同时记录 Agent trace 和任务管理器可持久化的阶段状态。"""
            trace_collector.record_stage(stage, retrieved_count)
            on_stage(stage, retrieved_count)

        try:
            response = services.chat_agent.run(state, stage_callback=record_stage)
        except Exception as error:
            trace_collector.record_error(error)
            raise
        trace_collector.record_state(state)
        return response

    def process_general(request: ChatRequest, _on_stage: StageCallback) -> ChatResponse:
        """保留确认后通用回答的服务形状；黄金首轮不自动确认该分支。"""
        user_image = (
            ChatImage(request.image, request.image_media_type or "")
            if request.image is not None
            else None
        )
        return services.chat_agent.answer_general(request.question_text, user_image)

    manager = ChatTaskManager(
        single_worker_settings,
        status_store,
        process_chat,
        process_general,
        execution_logger=_NullExecutionLogger(),
    )
    results: list[EvaluationCaseResult] = []
    manager.start()
    try:
        for case in cases:
            results.append(
                _evaluate_case(
                    case,
                    trace_collector,
                    services,
                    manager,
                    status_store,
                )
            )
    finally:
        manager.stop()

    metrics, category_rates = compute_metrics(results)
    return EvaluationRunReport(
        run_id=str(uuid4()),
        started_at=started.isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        document_id=services.document_id,
        dataset_sha256=dataset_sha256,
        answer_model=services.answer_model,
        scoring_model=services.scoring_model,
        prompt_version=services.prompt_version,
        case_count=len(results),
        metrics=metrics,
        category_pass_rates=category_rates,
        failed_case_ids=[result.case_id for result in results if not result.passed],
        passed=report_passed(results, category_rates),
        cases=results,
    )


def _evaluate_case(
    case: EvaluationCase,
    trace_collector: EvaluationTraceCollector,
    services: EvaluationServices,
    manager: ChatTaskManager,
    status_store: _MemoryChatStatusStore,
) -> EvaluationCaseResult:
    """执行一条样例并将运行、硬断言和评分错误保留在该条结果中。"""
    input_image = _read_input_image(case, services.fixture_root)
    trace = trace_collector.begin_case(case, input_image)
    request = ChatRequest(
        case.question,
        input_image.data if input_image is not None else None,
        input_image.media_type if input_image is not None else None,
    )
    accepted = manager.submit(request)
    trace.chat_id = accepted.chat_id
    record = manager.get_status(accepted.chat_id)
    while record.status not in TERMINAL_STATUSES:
        time.sleep(POLL_INTERVAL_SECONDS)
        record = manager.get_status(accepted.chat_id)

    trace_collector.record_statuses(status_store.history(accepted.chat_id))
    trace_collector.record_final(record)
    evidence = trace.retrieved_chunks
    response = record.response
    deterministic_checks = _deterministic_checks(case, trace, services)
    score_result = None
    total_score = None
    score_ok = False
    error = None

    if response is None:
        error = trace.error_code or record.error or "问答执行未生成响应"
    else:
        try:
            score_result = services.scoring_agent.score_answer(
                case,
                response,
                evidence,
                input_image=input_image,
            )
            total_score = weighted_score(score_result.dimension_scores)
            score_ok = scoring_passed(
                score_result.dimension_scores,
                score_result.fatal_flags,
            )
        except Exception as scoring_error:
            error = f"评分失败：{type(scoring_error).__name__}"

    passed = (
        error is None
        and response is not None
        and score_ok
        and _checks_pass(deterministic_checks)
    )
    return EvaluationCaseResult(
        case_id=case.id,
        category=case.category,
        trace=trace.to_dict(),
        deterministic_checks=deterministic_checks,
        scoring=score_result,
        weighted_score=total_score,
        score_passed=score_ok,
        passed=passed,
        error=error,
    )


def _deterministic_checks(
    case: EvaluationCase,
    trace: _TraceState,
    services: EvaluationServices,
) -> dict[str, bool | None]:
    """根据人工标注页码和行为类型，执行不依赖大模型的验收断言。"""
    expected_status = (
        ChatStatus.NEEDS_CONFIRMATION.value
        if case.expected_behavior == "abstain"
        else ChatStatus.COMPLETED.value
    )
    expected_response_route = (
        "needs_confirmation"
        if case.expected_behavior == "abstain"
        else case.expected_route
    )
    expected_stages = _expected_stages(case)
    statuses = [item.get("status") for item in trace.status_history]
    status_transitions_valid = (
        len(statuses) >= 2
        and statuses[0] == ChatStatus.ACCEPTED.value
        and statuses[-1] == expected_status
        and all(status == ChatStatus.RUNNING.value for status in statuses[1:-1])
    )
    retrieved = trace.retrieved_chunks
    hit_at_5 = None
    if case.evidence_pages:
        hit_at_5 = any(
            chunk["document_id"] == services.document_id
            and chunk["page_number"] in case.evidence_pages
            for chunk in retrieved[:5]
        )

    citations = trace.citations
    if case.expected_route == "out_of_scope" or case.expected_behavior == "abstain":
        citations_valid = not citations
    else:
        retrieved_by_id = {chunk["chunk_id"]: chunk for chunk in retrieved}
        cited_pages = {citation.get("page_number") for citation in citations}
        page_coverage_valid = (
            set(case.evidence_pages).issubset(cited_pages)
            if case.expected_behavior == "report_conflict"
            else bool(cited_pages.intersection(case.evidence_pages))
        )
        citations_valid = bool(citations) and page_coverage_valid and all(
            citation.get("chunk_id") in retrieved_by_id
            and citation.get("document_id") == services.document_id
            and citation.get("file_name")
            == retrieved_by_id[citation["chunk_id"]]["file_name"]
            and citation.get("page_number")
            == retrieved_by_id[citation["chunk_id"]]["page_number"]
            and citation.get("page_number") in case.evidence_pages
            for citation in citations
        )

    image_delivery = (
        trace.user_image_sent if case.input_image is not None else None
    )
    image_segments = [
        segment for segment in trace.answer_segments if segment.get("type") == "image"
    ]
    image_required = case.expected_behavior == "mixed_media"
    mixed_media_shape = None
    if image_required:
        has_text = any(
            segment.get("type") == "text"
            and isinstance(segment.get("text"), str)
            and bool(segment["text"].strip())
            for segment in trace.answer_segments
        )
        mixed_media_shape = has_text and bool(image_segments)
    asset_ownership: bool | None = None
    if image_required:
        asset_ownership = bool(image_segments)
    elif image_segments:
        asset_ownership = True
    validator = services.image_validator
    for segment in image_segments:
        asset_valid = (
            segment.get("document_id") == services.document_id
            and segment.get("page_number") in case.evidence_pages
            and validator is not None
            and validator(services.document_id, segment.get("asset_id", ""))
        )
        asset_ownership = bool(asset_ownership) and asset_valid

    if case.expected_route == "out_of_scope":
        retrieval_behavior = trace.embedding_calls == 0 and trace.vector_search_calls == 0
    else:
        retrieval_behavior = trace.embedding_calls > 0 and trace.vector_search_calls > 0
    checks = {
        "route": trace.route == case.expected_route,
        "retrieval_behavior": retrieval_behavior,
        "hit_at_5": hit_at_5,
        "citations": citations_valid,
        "status_transitions": status_transitions_valid,
        "response_route": trace.response_route == expected_response_route,
        "final_status": trace.final_status == expected_status,
        "stages": trace.stages == expected_stages,
        "user_image_delivery": image_delivery,
        "mixed_media_shape": mixed_media_shape,
        "asset_ownership": asset_ownership,
    }
    checks["deterministic_all"] = _checks_pass(checks)
    return checks


def _expected_stages(case: EvaluationCase) -> list[str]:
    """由标注的路由和行为得到必须实际执行的 Agent 阶段序列。"""
    stages = [ChatStage.SCOPE_AND_REWRITE.value]
    if case.expected_route == "out_of_scope":
        return stages
    stages.extend([ChatStage.RETRIEVAL.value, ChatStage.EVIDENCE_CHECK.value])
    stages.append(
        ChatStage.AWAITING_CONFIRMATION.value
        if case.expected_behavior == "abstain"
        else ChatStage.ANSWER_GENERATION.value
    )
    return stages


def _checks_pass(checks: dict[str, bool | None]) -> bool:
    """忽略不适用指标，只要任一适用确定性断言失败就返回 False。"""
    applicable = [value for value in checks.values() if value is not None]
    return bool(applicable) and all(applicable)


def _read_input_image(case: EvaluationCase, fixture_root: Path) -> ChatImage | None:
    """读取已校验的图片 fixture，并依据扩展名确定传输 MIME 类型。"""
    if case.input_image is None:
        return None
    root = fixture_root.resolve()
    path = root.joinpath(*case.input_image.replace("\\", "/").split("/")).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"用例 {case.id} 的图片 fixture 不存在或越界")
    media_type, _ = mimetypes.guess_type(path.name)
    if not media_type or not media_type.startswith("image/"):
        raise ValueError(f"用例 {case.id} 的图片 fixture 没有有效 image MIME 类型")
    return ChatImage(path.read_bytes(), media_type)


class _TracingDocumentStore:
    """将评测检索范围限制在用户指定的单个 completed PDF。"""

    def __init__(self, document: DocumentRecord) -> None:
        """保存已验证完成的目标文档。"""
        self._document = document

    def list_completed_documents(self) -> list[DocumentRecord]:
        """仅返回本轮评测指定的文档，避免其他 PDF 污染检索结果。"""
        return [self._document]


def _build_services(
    document_id: str, trace_collector: EvaluationTraceCollector
) -> tuple[EvaluationServices, object]:
    """从 .env 构建真实 Redis、Milvus、BGE-M3 和 Qwen3-VL 评测依赖。"""
    settings = Settings.load()
    required = {
        "RAG_REDIS_URL": settings.redis_url,
        "RAG_EMBEDDING_BASE_URL": settings.embedding_base_url,
        "RAG_CHAT_BASE_URL": settings.chat_base_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ConfigurationError("黄金集评测缺少服务配置：" + ", ".join(missing))
    settings.initialize_data_directories()

    from redis import Redis

    redis_client = Redis.from_url(settings.redis_url or "")
    try:
        document_store = RedisDocumentStore(redis_client)
        record = document_store.get(document_id)
        if record is None or record.status is not DocumentStatus.COMPLETED:
            raise ValueError("评测 document_id 必须对应 Redis 中已完成的 PDF")

        model = OpenAICompatibleChatClient(settings.chat_base_url or "", settings.chat_model)
        chat_agent = ChatAgent(
            _TracingChatModel(model, trace_collector),
            _TracingEmbeddingClient(
                OpenAIEmbeddingClient(settings.embedding_base_url or "", settings.embedding_model),
                trace_collector,
            ),
            _TracingVectorStore(
                MilvusChunkStore.from_path(settings.milvus_path), trace_collector
            ),
            _TracingDocumentStore(record),
            settings=settings,
        )
        scoring_agent = ScoringAgent(model)
        profile = "\n".join(value for value in (record.title, record.summary) if value)
        if not profile:
            profile = record.file_name

        def image_validator(owner: str, asset_id: str) -> bool:
            """用同一套受控 registry 规则验证回答图片的文档归属。"""
            return (
                load_asset_bytes(settings.parsed_dir, owner, asset_id) is not None
            )

        services = EvaluationServices(
            settings=settings,
            chat_agent=chat_agent,
            scoring_agent=scoring_agent,
            document_id=document_id,
            corpus_profile=profile,
            fixture_root=EVALUATION_ROOT,
            parsed_root=settings.parsed_dir,
            answer_model=settings.chat_model,
            scoring_model=settings.chat_model,
            prompt_version=PROMPT_VERSION,
            image_validator=image_validator,
        )
        return services, redis_client
    except Exception:
        redis_client.close()
        raise


def write_report(report: EvaluationRunReport, report_directory: Path) -> Path:
    """将完整 report 原子写入运行时报告目录，不修改黄金集文件。"""
    report_directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = report_directory / (
        f"evaluation-{stamp}-{report.document_id[:8]}-{report.run_id[:8]}.json"
    )
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


def main() -> None:
    """解析评测参数、构建服务、运行黄金集并以退出码报告结果。"""
    parser = argparse.ArgumentParser(description="运行多模态 RAG 黄金集评测")
    parser.add_argument("--document-id", required=True, help="Redis 中已完成示例 PDF 的 ID")
    parser.add_argument("--cases", type=Path, default=GOLDEN_CASES_PATH)
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "reports",
    )
    arguments = parser.parse_args()

    cases, dataset_sha256 = load_cases(arguments.cases)
    collector = EvaluationTraceCollector()
    services, redis_client = _build_services(arguments.document_id, collector)
    try:
        report = run_evaluation(
            cases,
            collector,
            services,
            dataset_sha256=dataset_sha256,
        )
        report_path = write_report(report, arguments.report_dir)
    finally:
        redis_client.close()

    print(
        json.dumps(
            {
                "report_path": str(report_path),
                "case_count": report.case_count,
                "passed": report.passed,
                "failed_case_ids": report.failed_case_ids,
                "metrics": {
                    name: metric.model_dump() for name, metric in report.metrics.items()
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if not report.passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
