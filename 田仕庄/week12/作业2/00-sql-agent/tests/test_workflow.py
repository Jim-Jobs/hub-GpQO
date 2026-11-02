import asyncio

import pytest

import sql_agent.workflow as workflow_module
from sql_agent.config import PROJECT_ROOT, settings
from sql_agent.db.catalog import SchemaCatalog
from sql_agent.db.executor import SQLExecutor
from sql_agent.db.validator import SQLValidator
from sql_agent.llm import _unwrap
from sql_agent.models import ErrorCode, FinalAnswer, SQLDraft, TableSelection
from sql_agent.workflow import SQLAgentWorkflow

# 用绝对路径，测试结果不随 cwd 变化（从仓库任意层级跑 pytest 都成立）。
CHINOOK_DB = PROJECT_ROOT / "ref" / "chinook.db"


class FakeChain:
    """只实现 ainvoke 的鸭子类型链：不构造真实 LangChain 链，不碰 key 与网络。"""

    def __init__(self, name, outputs, calls):
        self.name = name
        self._outputs = outputs
        self._calls = calls

    async def ainvoke(self, payload, config=None):
        self._calls.append((self.name, payload))
        return self._outputs.pop(0)


class FlakyChain:
    """第一次调用抛解析错误，之后返回正确值：验证 workflow 会重试一次。"""

    def __init__(self, error, value):
        self._error = error
        self._value = value
        self.calls = 0

    async def ainvoke(self, payload, config=None):
        self.calls += 1
        if self.calls == 1:
            raise self._error
        return self._value


class AlwaysFailChain:
    """每次调用都抛解析错误：验证重试耗尽后的用户可读报错。"""

    def __init__(self, error):
        self._error = error
        self.calls = 0

    async def ainvoke(self, payload, config=None):
        self.calls += 1
        raise self._error


@pytest.fixture(scope="module")
def workflow() -> SQLAgentWorkflow:
    catalog = SchemaCatalog(CHINOOK_DB)
    return SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=SQLExecutor(catalog.db_path),
    )


def _patch_chains(monkeypatch, outputs):
    """按 workflow 的实际调用顺序回放预设输出。"""

    calls: list[tuple[str, dict]] = []
    queue = list(outputs)

    factories = {
        "table_selector": "get_table_selector_chain",
        "sql_generator": "get_sql_generator_chain",
        "sql_rewriter": "get_sql_rewriter_chain",
        "result_summary": "get_result_summary_chain",
    }
    for short, func_name in factories.items():
        monkeypatch.setattr(
            workflow_module,
            func_name,
            lambda short=short: FakeChain(short, queue, calls),
        )
    return calls


def test_happy_path(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    calls = _patch_chains(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]
    assert result.row_count == 1
    assert result.answer == "共 347 张专辑。"
    assert len(calls) == 3


def test_rewrite_path(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(ArtistId FROM albums", explanation="bad"),
        SQLDraft(sql="SELECT COUNT(ArtistId) AS n FROM albums", explanation="fixed"),
        FinalAnswer(answer="共有 275 位艺人。", evidence=["275"]),
    ]
    _patch_chains(monkeypatch, outputs)

    result = asyncio.run(workflow.run("专辑里有多少位不同的艺人？"))

    assert result.repair_attempts == 1
    assert result.error is None
    assert "COUNT(ArtistId)" in (result.sql or "")


def test_empty_result_skips_summary_agent(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="查不存在的专辑", confidence=0.9),
        SQLDraft(sql="SELECT * FROM albums WHERE AlbumId = -1", explanation="empty"),
    ]
    calls = _patch_chains(monkeypatch, outputs)

    result = asyncio.run(workflow.run("编号为 -1 的专辑是什么？"))

    assert result.row_count == 0
    assert result.answer == "未查询到符合条件的数据。"
    assert len(calls) == 2


def test_unknown_table_is_rejected(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["not_a_table"], reason="bad", confidence=0.1),
        TableSelection(tables=["albums"], reason="fixed", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_chains(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]


def test_max_repair_attempts_returns_error(monkeypatch) -> None:
    catalog = SchemaCatalog(CHINOOK_DB)
    workflow = SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=SQLExecutor(catalog.db_path),
        max_repair_attempts=0,
    )
    outputs = [
        TableSelection(tables=["albums"], reason="bad sql", confidence=0.9),
        SQLDraft(sql="SELECT nope FROM albums", explanation="bad"),
    ]
    _patch_chains(monkeypatch, outputs)

    result = asyncio.run(workflow.run("测试失败路径"))

    assert result.error is not None
    assert result.repair_attempts == 0
    assert "最大修复次数" in result.answer


# ------------------------------------------------------------------ 迁移新增


def test_prompt_payload_keys(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    """锁死每条链的入参键集合，模板变量改名会在测试期暴露而非运行期。"""

    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    calls = _patch_chains(monkeypatch, outputs)

    asyncio.run(workflow.run("总共有多少张专辑？"))

    assert set(calls[0][1]) == {"question", "catalog", "feedback"}
    assert set(calls[1][1]) == {"question", "schema"}
    assert set(calls[2][1]) == {
        "question",
        "sql",
        "columns",
        "row_count",
        "truncated",
        "max_rows",
        "rows",
    }


def test_error_code_passed_as_value(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    """修复链必须收到 "syntax"，而不是 "ErrorCode.SYNTAX"。"""

    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(ArtistId FROM albums", explanation="bad"),
        SQLDraft(sql="SELECT COUNT(ArtistId) AS n FROM albums", explanation="fixed"),
        FinalAnswer(answer="共有 275 位艺人。", evidence=["275"]),
    ]
    calls = _patch_chains(monkeypatch, outputs)

    asyncio.run(workflow.run("专辑里有多少位不同的艺人？"))

    rewrite_payload = next(payload for name, payload in calls if name == "sql_rewriter")
    assert rewrite_payload["code"] == "syntax"
    assert str(ErrorCode.SYNTAX) == "syntax"
    assert f"{ErrorCode.SYNTAX}" == "syntax"


def test_unwrap_rejects_empty() -> None:
    """`structured()` 里唯一自写的逻辑必须完全离线可测。"""

    unwrap = _unwrap(TableSelection)
    good = TableSelection(tables=["albums"], reason="ok")

    assert unwrap.invoke([good]) is good
    with pytest.raises(TypeError):
        unwrap.invoke([])
    with pytest.raises(TypeError):
        unwrap.invoke([SQLDraft(sql="SELECT 1")])


def test_settings_db_path_exists() -> None:
    assert settings.db_path.exists()


# ------------------------------------------------------------------ 缺陷回归


def test_selection_exhaustion_returns_friendly_clarification(
    workflow: SQLAgentWorkflow, monkeypatch
) -> None:
    """选表预算耗尽时，用户看到澄清话术而不是写给模型的修复反馈。"""

    _patch_chains(
        monkeypatch,
        [
            TableSelection(
                tables=["albums", "customers"], reason="x", confidence=0.4
            ),
            TableSelection(
                tables=["albums", "customers"], reason="y", confidence=0.3
            ),
        ],
    )

    result = asyncio.run(workflow.run("买过摇滚专辑的客户是谁"))

    assert result.error is None
    assert "请换一种问法" in result.answer
    # 内部反馈词不得泄漏进用户答案。
    assert "外键" not in result.answer
    assert "JOIN" not in result.answer


def test_structured_parse_failure_retries_once(
    workflow: SQLAgentWorkflow, monkeypatch
) -> None:
    """链第一次解析失败（如模型没调工具）应重试一次并继续正常流程。"""

    flaky = FlakyChain(TypeError("tool_calls 为空"), TableSelection(tables=["albums"]))
    good = FakeChain(
        "gen",
        [SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count")],
        [],
    )
    monkeypatch.setattr(workflow_module, "get_table_selector_chain", lambda: flaky)
    monkeypatch.setattr(workflow_module, "get_sql_generator_chain", lambda: good)
    monkeypatch.setattr(
        workflow_module,
        "get_sql_rewriter_chain",
        lambda: FakeChain("rw", [], []),
    )
    monkeypatch.setattr(
        workflow_module,
        "get_result_summary_chain",
        lambda: FakeChain(
            "sum", [FinalAnswer(answer="共 347 张专辑。", evidence=["347"])], []
        ),
    )

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]
    assert flaky.calls == 2


def test_structured_parse_failure_message_is_clean(
    workflow: SQLAgentWorkflow, monkeypatch
) -> None:
    """两次都失败时报错面向用户，不携带 pydantic 原始细节与链接。"""

    boom = AlwaysFailChain(TypeError("tool_calls 为空"))
    monkeypatch.setattr(workflow_module, "get_table_selector_chain", lambda: boom)
    for name in (
        "get_sql_generator_chain",
        "get_sql_rewriter_chain",
        "get_result_summary_chain",
    ):
        monkeypatch.setattr(
            workflow_module, name, lambda name=name: FakeChain(name, [], [])
        )

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is not None
    assert "结构化结果" in result.answer
    assert "pydantic.dev" not in result.answer
    assert "validation error" not in result.answer
    assert boom.calls == 2
