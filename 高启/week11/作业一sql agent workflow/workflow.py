"""连接七个步骤，并在 SQL 校验或执行失败时有限次改写。"""

from typing import Any

from openai import OpenAI
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from database import execute_sql, parse_database, validate_sql
from model_steps import generate_sql, rewrite_sql, select_tables, summarize_result


def run_workflow(
    question: str,
    engine: Engine,
    client: OpenAI,
    model: str,
    max_rewrites: int = 2,
) -> dict[str, Any]:
    """执行完整问答，返回选表结果、SQL、原始数据和回答。

    Args:
        question: 用户的自然语言问题。
        engine: 只读数据库引擎。
        client: 模型客户端。
        model: 模型名称。
        max_rewrites: 首次生成之后允许改写的最大次数。

    Raises:
        ValueError: 输入、选表结果或模型输出不符合要求。
        RuntimeError: 达到 SQL 改写上限后仍失败。

    接口鉴权、网络或汇总失败会直接向上传递，不触发 SQL 改写。
    """
    if not question.strip():
        raise ValueError("用户问题不能为空。")
    if max_rewrites < 0:
        raise ValueError("max_rewrites 不能小于零。")

    print("[1] 解析数据库")
    schema = parse_database(engine)

    print("[2] 选择相关表")
    tables = select_tables(question, schema, client, model)
    print("所选表：", tables)
    if not tables:
        raise ValueError("没有选择到能回答问题的表。")
    selected_schema = {name: schema[name] for name in tables}

    print("[3] 生成 SQL")
    sql = generate_sql(question, selected_schema, client, model)

    for attempt in range(max_rewrites + 1):
        print("\n当前 SQL：\n", sql)
        print("[4] 校验 SQL")
        valid, error = validate_sql(engine, sql)

        if valid:
            print("[6] 执行 SQL")
            try:
                query_result = execute_sql(engine, sql)
            except SQLAlchemyError as exc:
                error = str(exc)
            else:
                print("[7] 汇总结果")
                answer = summarize_result(question, sql, query_result, client, model)
                return {
                    "tables": tables,
                    "sql": sql,
                    "query_result": query_result,
                    "answer": answer,
                }

        print("查询失败：", error)
        if attempt == max_rewrites:
            raise RuntimeError(f"达到 SQL 改写上限，最后错误：{error}")

        print("[5] 根据错误改写 SQL")
        sql = rewrite_sql(question, selected_schema, sql, error, client, model)

    raise RuntimeError("工作流未产生结果。")
