"""workflow 编排器:驱动 7 步 SQL Agent 流程,以 async generator 产出事件。

事件协议(SSE 层直接序列化):
- {"event": "step", "step": 1..7, "name": 步骤中文名, "status": "start"|"done", "data": {...}}
- {"event": "final", "status": "success"|"failed", ...全量结果}

第 5 步改写是一个循环:校验/执行失败 → 带错误反馈改写 → 重新校验,
上限 max_rewrite_rounds;直通时第 5 步产出 rounds=0 的 done 事件。
"""
import time
from typing import AsyncGenerator

from app.config import settings
from app.pipeline import steps

# 步骤编号 → 中文名(前端直接展示)
STEP_NAMES = {
    1: "数据库解析",
    2: "Table 选择",
    3: "SQL 生成",
    4: "SQL 校验",
    5: "SQL 改写",
    6: "SQL 执行",
    7: "结果汇总",
}


def _step(step_no: int, status: str, **data) -> dict:
    return {
        "event": "step",
        "step": step_no,
        "name": STEP_NAMES[step_no],
        "status": status,
        "data": data,
    }


async def run_workflow(question: str, parser, conn) -> AsyncGenerator[dict, None]:
    """驱动完整 workflow,逐步产出事件;不抛异常,失败以 final 事件收尾。"""
    started = time.time()
    rewrite_rounds = 0
    tables: list[str] = []
    sql = ""
    result: dict = {}
    summary = ""

    # 第 1 步:数据库解析(parser 启动时已缓存,此处汇报结果)
    yield _step(1, "start")
    yield _step(
        1, "done",
        tables=len(parser.business_tables),
        relations=len(parser.foreign_keys),
        table_names=parser.business_tables,
    )

    try:
        # 第 2 步:table 选择(空结果兜底为全部业务表)
        yield _step(2, "start")
        tables = await steps.select_tables(question, parser)
        fallback = False
        if not tables:
            tables = list(parser.business_tables)
            fallback = True
        yield _step(2, "done", tables=tables, fallback=fallback)

        # 第 3 步:SQL 生成
        yield _step(3, "start")
        sql = await steps.generate_sql(question, tables, parser)
        yield _step(3, "done", sql=sql)

        # 第 4~6 步:校验 → (失败则改写,上限 N 次) → 执行
        errors: list[str] = []
        fail_stage = "validate"  # 失败源头:校验(validate)或执行(execute)
        executed = False
        while True:
            yield _step(4, "start")
            ok, errors = steps.validate_sql(sql, conn)
            yield _step(4, "done", ok=ok, errors=errors)

            if ok:
                # 校验通过 → 改写环节结束(未触发),执行
                yield _step(5, "done", rounds=rewrite_rounds, triggered=rewrite_rounds > 0, errors=[])
                yield _step(6, "start")
                result = steps.execute_query(sql, conn, row_limit=settings.row_limit)
                if "error" not in result:
                    yield _step(
                        6, "done",
                        columns=result["columns"],
                        row_count=result["row_count"],
                        truncated=result["truncated"],
                    )
                    executed = True
                    break
                errors = [result["error"]]
                fail_stage = "execute"
                yield _step(6, "done", ok=False, errors=errors)

            # 改写:超出上限即失败收尾
            if rewrite_rounds >= settings.max_rewrite_rounds:
                yield _step(5, "done", rounds=rewrite_rounds, triggered=True, errors=errors)
                break

            rewrite_rounds += 1
            yield _step(5, "start")
            sql = await steps.rewrite_sql(question, tables, sql, errors, parser)
            yield _step(5, "done", rounds=rewrite_rounds, triggered=True, errors=errors, sql=sql)

        if not executed:
            stage_text = (
                "SQL 执行错误未能在改写重试内修复"
                if fail_stage == "execute"
                else "SQL 校验错误未能在改写重试内修复"
            )
            yield {
                "event": "final",
                "status": "failed",
                "question": question,
                "tables": tables,
                "sql": sql,
                "error": f"{stage_text}: " + "; ".join(errors),
                "rewrite_rounds": rewrite_rounds,
                "duration_ms": int((time.time() - started) * 1000),
            }
            return

        # 第 7 步:结果汇总
        yield _step(7, "start")
        summary = await steps.summarize(question, sql, result, settings.row_limit)
        yield _step(7, "done", summary=summary)

        yield {
            "event": "final",
            "status": "success",
            "question": question,
            "tables": tables,
            "sql": sql,
            "columns": result["columns"],
            "rows": result["rows"],
            "row_count": result["row_count"],
            "truncated": result["truncated"],
            "rewrite_rounds": rewrite_rounds,
            "summary": summary,
            "duration_ms": int((time.time() - started) * 1000),
        }
    except Exception as e:  # 兜底:任何未预期异常以 failed 收尾
        yield {
            "event": "final",
            "status": "failed",
            "question": question,
            "tables": tables,
            "sql": sql,
            "error": f"workflow 异常: {e}",
            "rewrite_rounds": rewrite_rounds,
            "duration_ms": int((time.time() - started) * 1000),
        }
