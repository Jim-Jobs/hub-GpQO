"""评测脚本：用 golden 问题集衡量「执行准确率 EX」。

用例与结果都落库：``eval_case`` 存用例，每次运行写 ``eval_run`` + ``eval_result``。
JSONL 仅作为导入源，导入后打上 ``src:<文件名>`` 标签，便于按评测集区分。

用法：
    python eval.py                       # 运行库内全部启用用例（库空时先导入默认集）
    python eval.py eval_multiturn.jsonl  # 导入该文件后再运行（可重复执行，按问题去重）

用例字段：
    question / user                     当前问句与用户
    setup                               可选，前置轮次（顺序在同一会话中执行）
    expected_sql                        期望结果（与 question 的执行结果做多重集比对）
    expect_dsl                          期望 DSL 子集（校验上下文改写是否正确补全）
    expect_status                       期望状态（clarify / denied 等）
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from chatbi import executor
from chatbi.db.models.eval import EvalCase, EvalResult, EvalRun
from chatbi.pipeline import ChatBI


def canon(rows) -> list:
    """把结果集规范化为可比较的字符串多重集（忽略列顺序/行顺序，数值保留两位）。"""
    out = []
    for r in rows:
        cells = []
        for v in r:
            if isinstance(v, bool):
                cells.append(str(int(v)))
            elif isinstance(v, (int, float)):
                cells.append(f"{round(float(v), 2):.2f}")
            elif v is None:
                cells.append("null")
            else:
                cells.append(str(v))
        out.append("|".join(cells))
    return sorted(out)


def match_subset(expected, actual) -> bool:
    """判断 actual 是否包含 expected 的全部键值（支持嵌套 dict 的子集匹配）。"""
    if not isinstance(actual, dict):
        return False
    for k, v in expected.items():
        if k not in actual:
            return False
        if isinstance(v, dict):
            if not match_subset(v, actual[k]):
                return False
        elif actual[k] != v:
            return False
    return True


# ---------------- 用例入库 ----------------

def _source(path: Path) -> str:
    return f"src:{path.name}"


def sync_cases(db, path: Path) -> int:
    """把 JSONL 用例导入/更新到 ``eval_case``，返回处理条数。"""
    raw = [json.loads(line) for line in
           path.read_text(encoding="utf-8").splitlines() if line.strip()]
    source = _source(path)
    known = {(c.question, tuple(c.setup_turns or []), c.user_id): c
             for c in db.scalars(select(EvalCase)) if source in (c.tags or [])}
    for item in raw:
        setup = list(item.get("setup") or [])
        user = item.get("user", "alice")
        case = known.get((item["question"], tuple(setup), user))
        if case is None:
            case = EvalCase(question=item["question"], user_id=user,
                            domain=item.get("domain", "default"),
                            setup_turns=setup, tags=[source])
            db.add(case)
        case.expected_dsl = item.get("expect_dsl")
        case.expected_sql = item.get("expected_sql")
        case.expect_status = item.get("expect_status")
        case.tolerance = float(item.get("tolerance", 0.01))
        case.tags = sorted(set((case.tags or []) + [source] + list(item.get("tags") or [])))
    db.commit()
    return len(raw)


def load_cases(db, source: str | None) -> list[dict]:
    rows = db.scalars(
        select(EvalCase).where(EvalCase.enabled.is_(True)).order_by(EvalCase.case_id)
    ).all()
    if source:
        rows = [c for c in rows if source in (c.tags or [])]
    return [{
        "case_id": c.case_id,
        "question": c.question,
        "user": c.user_id,
        "setup": list(c.setup_turns or []),
        "expected_sql": c.expected_sql,
        "expect_dsl": c.expected_dsl,
        "expect_status": c.expect_status,
    } for c in rows]


# ---------------- 执行 ----------------

def run_case(chatbi: ChatBI, case: dict) -> tuple[dict, str]:
    """执行一个用例，返回 (结果, 展示用问句)。带 setup 的走多轮会话入口。"""
    q = case["question"]
    user = case.get("user", "alice")
    setup = case.get("setup") or []
    if not setup:
        return chatbi.ask(q, user_id=user), q
    session_id = None
    for prev in setup:
        session_id = chatbi.chat(prev, user_id=user, session_id=session_id)["session_id"]
    return chatbi.chat(q, user_id=user, session_id=session_id), " → ".join(setup + [q])


def evaluate(cases: list[dict], chatbi: ChatBI, scope: str) -> int:
    db = chatbi.meta.db
    run = EvalRun(tenant_id="default", release_id=chatbi.release_id,
                  scope={"source": scope}, operator="cli", started_at=datetime.now())
    db.add(run)
    db.commit()      # 先落 run 行，避免长事务阻塞会话/审计的并发写入

    passed = 0
    for i, c in enumerate(cases, 1):
        res, shown = run_case(chatbi, c)
        expect_status = c.get("expect_status")
        expect_dsl = c.get("expect_dsl")

        if expect_status:
            ok = res["status"] == expect_status
            detail = f"期望状态={expect_status} 实际={res['status']}"
        elif expect_dsl is not None:
            ok = res["status"] == "ok" and match_subset(expect_dsl, res.get("dsl") or {})
            detail = "" if ok else (f"DSL 不符\n      期望包含: {expect_dsl}\n"
                                    f"      实际: {res.get('dsl')}")
        elif c.get("expected_sql"):
            if res["status"] != "ok":
                ok, detail = False, f"链路未返回结果：{res['status']} / {res.get('message', '')}"
            else:
                got = canon(executor.execute(chatbi.db_path, res["sql"])["rows"])
                exp = canon(executor.execute(chatbi.db_path, c["expected_sql"])["rows"])
                ok = got == exp
                detail = "" if ok else f"结果不一致\n      期望: {exp[:3]}\n      实际: {got[:3]}"
        else:
            ok, detail = False, "用例缺少 expected_dsl / expected_sql / expect_status"

        passed += 1 if ok else 0
        db.add(EvalResult(
            run_id=run.run_id, case_id=c["case_id"], passed=ok,
            actual_status=res["status"], actual_dsl=res.get("dsl"),
            actual_sql=res.get("sql"),
            diff={"detail": detail, "shown": shown} if not ok else None,
            elapsed_ms=res.get("elapsed_ms"),
        ))
        flag = "PASS" if ok else "FAIL"
        mode = (res.get("context") or {}).get("mode")
        suffix = f"  [{mode}]" if mode else ""
        print(f"[{flag}] #{i:02d} ({res.get('user', c.get('user', 'alice'))}) {shown}{suffix}")
        if not ok:
            print(f"      {detail}")
            if res.get("context"):
                print(f"      上下文: {res['context']['note']}")
            if res.get("sql"):
                print(f"      SQL: {res['sql']}")
            if res.get("hint"):
                print(f"      HINT: {res['hint']}")

    total = len(cases)
    ex = passed / total if total else 0.0
    run.total, run.passed, run.ex_rate = total, passed, ex
    run.finished_at = datetime.now()
    db.commit()

    print("\n" + "=" * 60)
    print(f"执行准确率 EX = {passed}/{total} = {ex:.1%}")
    print(f"评测批次 run#{run.run_id}  语义层批次 release#{chatbi.release_id}")
    print("=" * 60)
    return 0 if passed == total else 1


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 \
        else Path(__file__).parent / "eval_cases.jsonl"
    chatbi = ChatBI(use_llm=False)
    db = chatbi.meta.db
    sync_cases(db, path)
    cases = load_cases(db, _source(path))
    label = path.name
    print(f"评测集: {label}（{len(cases)} 条）")
    print(f"数据锚点日: {chatbi.anchor}\n")
    raise SystemExit(evaluate(cases, chatbi, label))


if __name__ == "__main__":
    main()
