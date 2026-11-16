"""验证黄金集加载、评分规范化和可注入的本地评测运行器。"""

import json

import pytest

from backend.app.agents.chat.models import RouteAndQuery
from backend.app.agents.chat.state import ChatState
from backend.app.chat.domain.enums import ChatStage
from backend.app.chat.domain.models import ChatImage, ChatResponse
from backend.app.core.config import Settings
from backend.app.documents.domain.models import ChunkRecord
from backend.app.agents.chat.models import EvidenceAssessment, RetrievedChunk
from backend.tests.evaluation.agents.scoring_agent import ScoringAgent, ScoringOutputError
from backend.tests.evaluation.dataset import (
    EVALUATION_ROOT,
    EvaluationDatasetError,
    _validate_image_fixture,
    load_cases,
)
from backend.tests.evaluation.metrics import scoring_passed, weighted_score
from backend.tests.evaluation.runner import (
    EvaluationServices,
    EvaluationTraceCollector,
    _TraceState,
    _deterministic_checks,
    run_evaluation,
    write_report,
)
from backend.tests.evaluation.schemas import (
    DimensionScores,
    EvaluationCase,
    ScoringResult,
)


def test_load_canonical_golden_dataset_and_image_fixture() -> None:
    """固定首轮 JSONL 通过字段、类别数量和用户图片路径检查。"""
    cases, dataset_sha256 = load_cases()

    assert len(cases) == 33
    assert len(dataset_sha256) == 64
    assert sum(case.input_image is not None for case in cases) == 2


def test_image_fixture_validator_rejects_path_traversal(tmp_path) -> None:
    """黄金用例不能借 input_image 读取 fixture 根目录之外的文件。"""
    with pytest.raises(EvaluationDatasetError, match="相对路径"):
        _validate_image_fixture("../secret.png", tmp_path, "MM-TEST")


def test_weighted_score_renormalizes_not_applicable_dimensions() -> None:
    """范围外用例只计完整性和行为分，80 分与 fatal 标记正确门控。"""
    scores = DimensionScores(
        correctness=None,
        grounding=None,
        completeness=5,
        behavior=4,
    )

    assert weighted_score(scores) == 93.33
    assert scoring_passed(scores, []) is True
    assert scoring_passed(scores, ["false_external_action"]) is False


class _FakeAgent:
    """为评测 runner 返回固定范围外结果，不访问网络或向量库。"""

    def run(self, state: ChatState, stage_callback=None) -> ChatResponse:
        """只报告范围判断阶段并在 ChatState 写入 out-of-scope 路由。"""
        if stage_callback is not None:
            stage_callback(ChatStage.SCOPE_AND_REWRITE, 0)
        state.route_and_query = RouteAndQuery("out_of_scope", "")
        state.response = ChatResponse(
            route="out_of_scope",
            answer_segments=[{"type": "text", "text": "这是通用回答，并引导回资料主题。"}],
        )
        return state.response

    def answer_general(self, question_text, user_image) -> ChatResponse:
        """提供接口形状；本用例不触发确认后的通用回答路径。"""
        raise AssertionError("范围外黄金用例不应触发确认后通用回答")


class _FakeScorer:
    """为 runner 提供固定的范围外维度评分。"""

    def score_answer(self, case, response, evidence, *, input_image=None) -> ScoringResult:
        """返回适用于 answer_and_guide 的两维评分。"""
        return ScoringResult(
            case_id=case.id,
            dimension_scores=DimensionScores(
                correctness=None,
                grounding=None,
                completeness=5,
                behavior=5,
            ),
            fatal_flags=[],
            missing_points=[],
            unsupported_claims=[],
            evidence_refs=[],
            rationale="回答并引导回资料库主题。",
        )


def test_runner_uses_chat_task_lifecycle_and_keeps_trace_test_only(tmp_path) -> None:
    """替身可完整经过 accepted/running/completed，并生成可序列化单条结果。"""
    case = EvaluationCase(
        id="SCOPE-TEST",
        category="scope",
        question="写一首春天的小诗",
        expected_route="out_of_scope",
        expected_behavior="answer_and_guide",
        required_answer_points=["通用回答并引导回资料主题"],
    )
    services = EvaluationServices(
        settings=Settings.from_env({"RAG_DATA_ROOT": str(tmp_path)}),
        chat_agent=_FakeAgent(),
        scoring_agent=_FakeScorer(),
        document_id="doc-eval",
        corpus_profile="五类 QA 示例",
        fixture_root=tmp_path,
        parsed_root=tmp_path,
        answer_model="fake-chat",
        scoring_model="fake-chat",
    )

    report = run_evaluation(
        [case],
        EvaluationTraceCollector(),
        services,
        dataset_sha256="a" * 64,
    )

    result = report.cases[0]
    assert report.case_count == 1
    assert result.passed is True
    assert result.trace["route"] == "out_of_scope"
    assert result.trace["stages"] == ["scope_and_rewrite"]
    assert result.trace["final_status"] == "completed"
    assert [item["status"] for item in result.trace["status_history"]] == [
        "accepted",
        "running",
        "completed",
    ]
    report_path = write_report(report, tmp_path / "reports")
    saved_report = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved_report["cases"][0]["trace"]["route"] == "out_of_scope"
    public_response = json.loads(ChatResponse("out_of_scope").to_json())
    assert "trace" not in public_response


class _GoldenFakeAgent:
    """按 JSONL 标注构造可重复的全通过响应，验证 33 条完整 runner。"""

    def __init__(self, cases, collector) -> None:
        """建立问题到标注用例的查找表和 trace 观察器。"""
        self._cases = {case.question: case for case in cases}
        self._collector = collector

    def run(self, state: ChatState, stage_callback=None) -> ChatResponse:
        """按标注路由、阶段、证据页、引用和多模态输入生成固定结果。"""
        case = self._cases[state.question_text]
        callback = stage_callback or (lambda _stage, _count: None)
        callback(ChatStage.SCOPE_AND_REWRITE, 0)
        state.route_and_query = RouteAndQuery(case.expected_route, "fake query")
        if case.expected_route == "out_of_scope":
            return ChatResponse(
                "out_of_scope",
                [{"type": "text", "text": "通用回答，并引导回资料库主题。"}],
            )

        callback(ChatStage.RETRIEVAL, 0)
        self._collector.record_embedding_call()
        self._collector.record_vector_search()
        state.retrieved_chunks = [
            RetrievedChunk(
                ChunkRecord(
                    f"{case.id}-page-{page}",
                    "doc-eval",
                    page,
                    index,
                    f"第 {page} 页测试证据",
                ),
                0.9 - index / 10,
                "fixture.pdf",
            )
            for index, page in enumerate(case.evidence_pages)
        ]
        callback(ChatStage.EVIDENCE_CHECK, len(state.retrieved_chunks))
        if case.expected_behavior == "abstain":
            state.evidence = EvidenceAssessment(False, "证据不足")
            callback(ChatStage.AWAITING_CONFIRMATION, len(state.retrieved_chunks))
            return ChatResponse(
                "needs_confirmation",
                [{"type": "text", "text": "没有足够依据，是否需要通用回答？"}],
            )

        state.evidence = EvidenceAssessment(True, "测试证据充足")
        callback(ChatStage.ANSWER_GENERATION, len(state.retrieved_chunks))
        if state.user_image is not None:
            self._collector.record_model_call([state.user_image])
        segments = [{"type": "text", "text": "预设的完整测试回答。"}]
        image_assets = []
        if case.expected_behavior == "mixed_media":
            asset = {
                "type": "image",
                "document_id": "doc-eval",
                "asset_id": "asset-valid",
                "file_name": "fixture.pdf",
                "page_number": case.evidence_pages[0],
            }
            segments.append(asset)
            image_assets.append({key: value for key, value in asset.items() if key != "type"})
        citations = [
            {
                "chunk_id": f"{case.id}-page-{page}",
                "document_id": "doc-eval",
                "file_name": "fixture.pdf",
                "page_number": page,
            }
            for page in case.evidence_pages
        ]
        return ChatResponse("in_scope", segments, citations, image_assets)

    def answer_general(self, question_text, user_image) -> ChatResponse:
        """拒绝意外触发黄金集未涵盖的通用回答分支。"""
        raise AssertionError("首轮黄金集不会接受通用回答确认")


class _GoldenFakeScorer:
    """按照评分契约为固定测试数据返回最高分。"""

    def score_answer(self, case, response, evidence, *, input_image=None) -> ScoringResult:
        """对范围外用例返回两个不适用维度，其他用例返回完整维度。"""
        out_of_scope = case.expected_behavior == "answer_and_guide"
        return ScoringResult(
            case_id=case.id,
            dimension_scores=DimensionScores(
                correctness=None if out_of_scope else 5,
                grounding=None if out_of_scope else 5,
                completeness=5,
                behavior=5,
            ),
            fatal_flags=[],
            missing_points=[],
            unsupported_claims=[],
            evidence_refs=[chunk["chunk_id"] for chunk in evidence],
            rationale="本地替身按用例预期返回通过评分。",
        )


def test_full_golden_run_passes_all_gates_with_local_fakes(tmp_path) -> None:
    """用假 Agent 和假评分器串行跑 33 条并验证报告整体验收门槛。"""
    cases, dataset_sha256 = load_cases()
    collector = EvaluationTraceCollector()
    services = EvaluationServices(
        settings=Settings.from_env({"RAG_DATA_ROOT": str(tmp_path)}),
        chat_agent=_GoldenFakeAgent(cases, collector),
        scoring_agent=_GoldenFakeScorer(),
        document_id="doc-eval",
        corpus_profile="固定黄金集替身资料范围",
        fixture_root=EVALUATION_ROOT,
        parsed_root=tmp_path,
        answer_model="fake-answer",
        scoring_model="fake-scoring",
        image_validator=lambda _document_id, _asset_id: True,
    )

    report = run_evaluation(
        cases,
        collector,
        services,
        dataset_sha256=dataset_sha256,
    )

    assert report.case_count == 33
    assert report.passed is True
    assert report.failed_case_ids == []
    assert report.metrics["hit_at_5"].denominator == 28
    assert report.metrics["user_image_delivery_accuracy"].denominator == 2


class _FakeModel:
    """记录评分调用图片并返回预设的 ScoringResult JSON。"""

    def __init__(self, payload: dict) -> None:
        """保存待返回数据并初始化调用图片记录。"""
        self.payload = payload
        self.images = None

    def complete(self, system_prompt, user_prompt, images=None) -> str:
        """返回预设评分 JSON 并记录评分模型收到的图像列表。"""
        self.images = list(images or [])
        return json.dumps(self.payload, ensure_ascii=False)


class _MalformedScoringModel:
    """模拟评分模型输出非 JSON 文本的解析故障。"""

    def complete(self, system_prompt, user_prompt, images=None) -> str:
        """返回不符合评分输出契约的内容。"""
        return "这不是 JSON"


def test_scoring_agent_validates_case_and_forwards_input_image() -> None:
    """多模态评分会收到用户图片，且模型返回 case_id 必须匹配。"""
    case = EvaluationCase(
        id="MM-TEST",
        category="multimodal_input",
        question="图中的手机是否支持无线充电？",
        expected_route="in_scope",
        expected_behavior="answer_from_text_and_image",
        required_answer_points=["不支持无线充电"],
        evidence_pages=[4],
        input_image="images/phone_table_page4.png",
    )
    payload = {
        "case_id": case.id,
        "dimension_scores": {
            "correctness": 5,
            "grounding": 5,
            "completeness": 5,
            "behavior": 5,
        },
        "fatal_flags": [],
        "missing_points": [],
        "unsupported_claims": [],
        "evidence_refs": ["chunk-1"],
        "rationale": "图中型号与文本证据共同支持结论。",
    }
    model = _FakeModel(payload)
    input_image = ChatImage(b"image", "image/png")

    result = ScoringAgent(model).score_answer(
        case,
        ChatResponse("in_scope", [{"type": "text", "text": "不支持无线充电"}]),
        [{"chunk_id": "chunk-1", "page_number": 4}],
        input_image=input_image,
    )

    assert result.case_id == case.id
    assert model.images == [input_image]

    model.payload["case_id"] = "OTHER-CASE"
    with pytest.raises(ScoringOutputError, match="case_id"):
        ScoringAgent(model).score_answer(
            case,
            ChatResponse("in_scope", [{"type": "text", "text": "不支持无线充电"}]),
            [{"chunk_id": "chunk-1", "page_number": 4}],
        )
    model.payload["case_id"] = case.id
    model.payload["evidence_refs"] = ["chunk-not-provided"]
    with pytest.raises(ScoringOutputError, match="未提供"):
        ScoringAgent(model).score_answer(
            case,
            ChatResponse("in_scope", [{"type": "text", "text": "不支持无线充电"}]),
            [{"chunk_id": "chunk-1", "page_number": 4}],
        )
    with pytest.raises(ScoringOutputError, match="ScoringResult JSON"):
        ScoringAgent(_MalformedScoringModel()).score_answer(
            case,
            ChatResponse("in_scope", [{"type": "text", "text": "不支持无线充电"}]),
            [{"chunk_id": "chunk-1", "page_number": 4}],
        )


def test_conflict_case_requires_citations_for_both_evidence_pages(tmp_path) -> None:
    """价格冲突答案必须同时引用标注的两页，单页不能通过确定性检查。"""
    case = EvaluationCase(
        id="TBL-04",
        category="TableQA",
        question="Mate40 的价格是多少，文档是否有冲突？",
        expected_route="in_scope",
        expected_behavior="report_conflict",
        required_answer_points=["第 3 页价格", "第 4 页价格", "说明冲突"],
        evidence_pages=[3, 4],
    )
    trace = _TraceState(case.id, case.expected_route, case.expected_behavior, None)
    trace.route = "in_scope"
    trace.stages = ["scope_and_rewrite", "retrieval", "evidence_check", "answer_generation"]
    trace.retrieved_chunks = [
        {
            "chunk_id": "chunk-page-3",
            "document_id": "doc-eval",
            "page_number": 3,
            "file_name": "fixture.pdf",
        }
    ]
    trace.citations = [
        {
            "chunk_id": "chunk-page-3",
            "document_id": "doc-eval",
            "page_number": 3,
            "file_name": "fixture.pdf",
        }
    ]
    trace.final_status = "completed"
    services = EvaluationServices(
        settings=Settings.from_env({"RAG_DATA_ROOT": str(tmp_path)}),
        chat_agent=_FakeAgent(),
        scoring_agent=_FakeScorer(),
        document_id="doc-eval",
        corpus_profile="表格价格",
        fixture_root=tmp_path,
        parsed_root=tmp_path,
        answer_model="fake-chat",
        scoring_model="fake-chat",
    )

    checks = _deterministic_checks(case, trace, services)

    assert checks["hit_at_5"] is True
    assert checks["citations"] is False
