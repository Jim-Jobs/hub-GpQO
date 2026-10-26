"""API 路由:SSE 问答流、历史列表、数据库 schema 概览。"""
import json

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from app import history
from app.pipeline import runner

router = APIRouter()


class AskRequest(BaseModel):
    """POST /api/ask 请求体。"""

    question: str


@router.post("/ask")
async def ask(body: AskRequest, request: Request) -> EventSourceResponse:
    """SSE 流式问答:逐步推送 7 步 workflow 事件,final 事件后落库历史。"""
    parser = request.app.state.parser
    conn = request.app.state.ro_conn

    async def event_stream():
        steps_summary: list[dict] = []
        async for ev in runner.run_workflow(body.question, parser, conn):
            # 收集步骤完成事件,作为历史回放的各步摘要
            if ev["event"] == "step" and ev["status"] == "done":
                steps_summary.append(ev)
            if ev["event"] == "final":
                history.save_record({**ev, "steps": steps_summary})
            yield {"event": "message", "data": json.dumps(ev, ensure_ascii=False)}

    return EventSourceResponse(event_stream())


@router.get("/history")
async def get_history(limit: int = 50) -> dict:
    """历史问答列表(倒序),前端左栏展示与回放。"""
    return {"items": history.list_records(limit=limit)}


@router.get("/schema")
async def get_schema(request: Request) -> dict:
    """数据库概览:业务表清单与字段名,前端展示数据字典。"""
    parser = request.app.state.parser
    tables = [
        {"name": name, "fields": list(parser.get_table_fields(name).index)}
        for name in parser.business_tables
    ]
    return {"tables": tables, "relations": len(parser.foreign_keys)}
