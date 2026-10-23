"""可选 LLM 解析器：把 NL→DSL 交给大模型，受控 schema 约束输出。

默认不启用：仅当设置 CHATBI_LLM_BASEURL / CHATBI_LLM_KEY / CHATBI_LLM_MODEL
环境变量且 use_llm=True 时生效；未启用时由 RuleParser 兜底。
"""
from __future__ import annotations

import json
import os
import urllib.request

_SYSTEM_PROMPT = """你是 ChatBI 的查询解析器。只能使用下面给出的受控指标与维度。
输出必须是 JSON 查询 DSL，metric 与 dimensions 只能从候选中选择。
若信息不足或有歧义，输出 {{"clarify": "需要用户澄清的问题"}}。

DSL 结构：
{{"metric": "指标名", "dimensions": ["维度名"], "filters": [{{"dimension":"维度名","op":"eq|in","value":...}}],
 "time_range": {{"type":"relative|absolute","value":"last_month", "start":"", "end":""}},
 "order_by": [{{"field":"指标名或维度名","direction":"desc"}}], "limit": 10}}

可用指标：
{metrics}

可用维度：
{dimensions}
"""


def llm_available() -> bool:
    return all(os.environ.get(k) for k in ("CHATBI_LLM_BASEURL", "CHATBI_LLM_KEY", "CHATBI_LLM_MODEL"))


def _build_prompt(registry) -> str:
    metrics = "\n".join(
        f"- {m.name}（{m.display_name}）: {m.expression}；可用维度: {', '.join(m.available_dimensions)}"
        for m in registry.metrics.values())
    dims = "\n".join(
        f"- {d.name}（{d.display_name}）: 取值 {', '.join(d.values) if d.values else '自由文本'}"
        for d in registry.dimensions.values())
    return _SYSTEM_PROMPT.format(metrics=metrics, dimensions=dims)


def parse_with_llm(question: str, registry, history: list | None = None) -> dict | None:
    """调用 OpenAI 兼容接口做 NL→DSL；失败返回 None。"""
    if not llm_available():
        return None
    base = os.environ["CHATBI_LLM_BASEURL"].rstrip("/")
    payload = {
        "model": os.environ["CHATBI_LLM_MODEL"],
        "temperature": 0,
        "messages": [
            {"role": "system", "content": _build_prompt(registry)},
            *[{"role": m["role"], "content": m["content"]} for m in (history or [])],
            {"role": "user", "content": question},
        ],
    }
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ['CHATBI_LLM_KEY']}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        obj = json.loads(content)
    except Exception:
        return None
    if "clarify" in obj:
        return {"action": "clarify", "message": obj["clarify"], "options": []}
    return {"action": "dsl", "dsl": obj, "trace": {"source": "llm"}}
