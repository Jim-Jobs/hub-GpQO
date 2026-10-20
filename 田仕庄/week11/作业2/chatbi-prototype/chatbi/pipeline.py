"""端到端链路：NL → DSL → SQL → 执行 → 图表 → 结论。"""
from __future__ import annotations

import hashlib
import uuid
from datetime import date
from pathlib import Path

from . import chart as chart_mod
from . import compiler, config, context, executor, summary
from .db.models.audit import QueryAudit
from .metastore import MetaStore
from .nlu.llm import parse_with_llm
from .nlu.parser import RuleParser
from .permission import DEFAULT_USER, load_permissions
from .semantic import (PermissionDenied, SemanticError, ValidationError,
                       load_registry, load_registry_from_db)
from .session import MAX_ROWS_KEPT, SessionStore, Turn
from .timeres import previous_period, resolve_time_range

ALLOWED_STATUS = {"ok", "clarify", "invalid", "denied", "error"}


class ChatBI:
    """ChatBI 原型主入口。"""

    def __init__(self, db_path=None, semantic_dir=None, permissions_file=None,
                 ref_date: date | None = None, use_llm: bool = False,
                 semantic_source: str = "auto", meta_db=None):
        self.db_path = Path(db_path or config.DEFAULT_DB)
        self.semantic_source = semantic_source
        self.meta = MetaStore(Path(meta_db or config.META_DB))
        self.registry = self._load_semantic(semantic_dir)
        self.permissions = load_permissions(Path(permissions_file or config.PERMISSIONS_FILE))
        self.parser = RuleParser(self.registry)
        self.use_llm = use_llm
        self.anchor = ref_date or self._data_anchor()
        rel = self.meta.latest_release()
        self.release_id = rel["release_id"] if rel else None
        # 多轮会话状态服务（落库在同一个元数据库）
        self.sessions = SessionStore(self.meta.db_path)

    # ---- 语义层加载：DB（已发布版本）优先，缺失时回退 YAML ----
    def _load_semantic(self, semantic_dir):
        if self.semantic_source in ("auto", "db"):
            registry = load_registry_from_db(self.meta)
            self.meta.db.rollback()   # 结束只读快照，避免长事务占住写锁
            if registry is not None:
                self.semantic_source = "db"
                return registry
            if self.semantic_source == "db":
                raise SemanticError(
                    f"元数据库 {self.meta.db_path} 中没有已发布的语义层定义")
        self.semantic_source = "yaml"
        return load_registry(Path(semantic_dir or config.SEMANTIC_DIR))

    # ---- 数据锚点：以库内最新业务日期为"今天"，使相对时间可用 ----
    def _data_anchor(self) -> date:
        v = executor.scalar(self.db_path, "SELECT MAX(InvoiceDate) AS m FROM invoices")
        return date.fromisoformat(str(v)[:10])

    def user(self, user_id: str):
        return self.permissions.get(user_id, DEFAULT_USER)

    # ---- 主流程 ----
    def ask(self, question: str, user_id: str = "alice", history: list | None = None,
            parsed: dict | None = None) -> dict:
        """单轮问数。``parsed`` 可由多轮上下文层预置，避免重复解析。"""
        perm = self.user(user_id)
        res = self._answer(question, perm, history, parsed)
        self._audit(question, res, perm)
        return res

    def _answer(self, question: str, perm, history: list | None,
                parsed: dict | None) -> dict:
        if parsed is None:
            if self.use_llm:
                parsed = parse_with_llm(question, self.registry, history)
            if parsed is None:
                parsed = self.parser.parse(question, self.anchor)

        if parsed.get("action") == "clarify":
            return {"status": "clarify", "question": question,
                    "message": parsed.get("message"), "options": parsed.get("options", []),
                    "user": perm.user_id}

        dsl = parsed["dsl"]

        try:
            if dsl.get("compare"):
                return self._ask_compare(question, dsl, perm, parsed.get("trace"))

            compiled = compiler.compile_dsl(dsl, self.registry, perm, self.anchor)
            result = executor.execute(self.db_path, compiled["sql"])
            rows = result["rows"]
            return {
                "status": "ok",
                "question": question,
                "user": perm.user_id,
                "dsl": dsl,
                "sql": compiled["sql"],
                "columns": result["columns"],
                "rows": rows,
                "chart": chart_mod.recommend(dsl, result["columns"], rows, self.registry),
                "summary": summary.summarize(dsl, rows, self.registry, compiled["resolved_time_range"]),
                "elapsed_ms": result["elapsed_ms"],
                "truncated": result["truncated"],
                "trace": parsed.get("trace"),
            }
        except PermissionDenied as e:
            return {"status": "denied", "question": question, "user": perm.user_id,
                    "message": str(e), "field": e.field, "dsl": dsl}
        except ValidationError as e:
            return {"status": "invalid", "question": question, "user": perm.user_id,
                    "message": e.error, "hint": e.hint, "dsl": dsl}
        except (SemanticError, executor.ExecutionError) as e:
            return {"status": "error", "question": question, "user": perm.user_id,
                    "message": str(e), "dsl": dsl}

    # ---- 多轮会话入口 ----
    def chat(self, question: str, user_id: str = "alice",
             session_id: str | None = None) -> dict:
        """在会话上下文中问数：先按上下文改写问句，执行后回写会话状态。

        ``session_id`` 为空或已过期时会自动新建会话，新会话 id 在返回值的
        ``session_id`` 字段中给出，前端后续请求带上它即可保持多轮。
        """
        perm = self.user(user_id)
        session = self.sessions.get_or_create(session_id, user_id)
        rw = context.rewrite(question, session, self.parser, self.anchor)

        parsed = {"action": "dsl", "dsl": rw.dsl, "trace": rw.trace} if rw.resolved else rw.clarify
        res = self._answer(question, perm, session.history(), parsed)

        turn = Turn(
            index=len(session.turns) + 1,
            question=question,
            status=res["status"],
            dsl=res.get("dsl"),
            mode=rw.mode,
            context_note=rw.note,
            columns=list(res.get("columns", [])),
            rows=[list(r) for r in res.get("rows", [])[:MAX_ROWS_KEPT]],
            summary=res.get("summary", ""),
            message=res.get("message", ""),
            options=list(res.get("options", [])),
            trace=rw.trace,
            sql=res.get("sql", ""),
            row_count=len(res.get("rows") or []),
            elapsed_ms=res.get("elapsed_ms"),
        )
        session.append(turn)

        res["session_id"] = session.session_id
        res["turn"] = turn.index
        res["context"] = {"mode": rw.mode, "note": rw.note}
        self._audit(question, res, perm)
        return res

    def reset(self, session_id: str) -> bool:
        """结束/清空一个会话（清空上下文）。"""
        return self.sessions.drop(session_id)

    # ---- 审计：一次问数一行，含权限快照与语义层批次号 ----
    def _audit(self, question: str, res: dict, perm):
        sql = res.get("sql")
        self.meta.db.add(QueryAudit(
            request_id=uuid.uuid4().hex,
            user_id=perm.user_id,
            session_id=res.get("session_id"),
            turn_index=res.get("turn"),
            question=question,
            dsl=res.get("dsl"),
            sql_text=sql,
            sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest() if sql else None,
            release_id=self.release_id,
            perm_snapshot={
                "user": perm.user_id,
                "row_rules": perm.row_rules,
                "denied_columns": sorted(perm.denied_columns),
                "masked_columns": sorted(perm.masked_columns),
            },
            row_count=len(res.get("rows") or []),
            elapsed_ms=res.get("elapsed_ms"),
            status=res["status"],
        ))
        self.meta.db.commit()

    # ---- 同环比 ----
    def _ask_compare(self, question: str, dsl: dict, perm, trace) -> dict:
        base = {k: v for k, v in dsl.items() if k != "compare"}
        prev_spec = previous_period(dsl["time_range"], self.anchor)

        now_dsl = dict(base)
        compiled = compiler.compile_dsl(now_dsl, self.registry, perm, self.anchor)
        cur = executor.execute(self.db_path, compiled["sql"])

        prev_dsl = dict(base, time_range=prev_spec)
        compiled_prev = compiler.compile_dsl(prev_dsl, self.registry, perm, self.anchor)
        prv = executor.execute(self.db_path, compiled_prev["sql"])

        cur_v = cur["rows"][0][-1] if cur["rows"] else None
        prv_v = prv["rows"][0][-1] if prv["rows"] else None
        delta = ratio = None
        if isinstance(cur_v, (int, float)) and isinstance(prv_v, (int, float)) and prv_v:
            delta = cur_v - prv_v
            ratio = delta / prv_v

        label = "环比" if dsl["compare"] == "mom" else "同比"
        metric_disp = self.registry.metric_display(dsl["metric"])
        text = f"{metric_disp}{label}：本期 {cur_v}，上期 {prv_v}"
        if ratio is not None:
            text += f"，{'增长' if ratio >= 0 else '下降'} {abs(ratio):.1%}"
        text += "。"

        return {
            "status": "ok",
            "question": question,
            "user": perm.user_id,
            "dsl": dsl,
            "sql": compiled["sql"],
            "compare_sql": compiled_prev["sql"],
            "columns": ["metric", "current", "previous", "delta", "ratio"],
            "rows": [[dsl["metric"], cur_v, prv_v, delta, ratio]],
            "chart": {"type": "number", "value": cur_v, "label": dsl["metric"]},
            "summary": text,
            "elapsed_ms": cur["elapsed_ms"] + prv["elapsed_ms"],
            "truncated": False,
            "trace": trace,
        }


def format_result(res: dict) -> str:
    """把 ask() 的返回渲染成一段便于阅读的文本（供 CLI / 评测使用）。"""
    lines = [f"[{res['status']}] {res['question']}"]
    if res["status"] == "clarify":
        lines.append(f"  需要澄清：{res.get('message')}")
        if res.get("options"):
            lines.append("  可选：" + " | ".join(res["options"]))
        return "\n".join(lines)
    if "dsl" in res:
        lines.append(f"  DSL: {res['dsl']}")
    if res["status"] != "ok":
        lines.append(f"  说明：{res.get('message', '')} {res.get('hint', '')}")
        return "\n".join(lines)
    lines.append(f"  SQL: {res['sql']}")
    lines.append(f"  列: {res['columns']}")
    for r in res["rows"][:10]:
        lines.append(f"    {r}")
    if len(res["rows"]) > 10:
        lines.append(f"    ...（共 {len(res['rows'])} 行）")
    lines.append(f"  图表: {res['chart']}")
    lines.append(f"  结论: {res['summary']}")
    lines.append(f"  耗时: {res['elapsed_ms']} ms")
    return "\n".join(lines)
