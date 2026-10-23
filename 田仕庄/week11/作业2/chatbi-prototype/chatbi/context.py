"""多轮上下文改写：把省略句 / 指代句补全为可执行的完整 DSL。

与 ``session.py`` 的分工：session 负责「记住什么」，这里负责「怎么用」。

识别的四类多轮场景
------------------
============ ==========================================================
模式          触发条件与行为
============ ==========================================================
``clarify_pick`` 上一轮在澄清，本轮输入是对候选项的直接选择 → 用澄清时已识别的
                 维度/时间槽位 + 本轮选定的指标，组装出完整 DSL
``followup``  本轮能解析出指标，但带追问标记（呢 / 那 / 换成 / 按…看）→ 用本轮
                 显式槽位覆盖上一轮，未提及的槽位（维度/时间/筛选/排序）继承
``borrow``    本轮解析不出指标，但识别到了维度/时间等槽位 → 指标沿用上一轮
``anaphora``  出现「这些 / 上面」等指代词 → 用上一轮结果集里该维度的取值生成筛选
``new``       无追问语义 → 视为全新问题，绝不继承上下文（避免上下文污染）
============ ==========================================================

每条改写都会给出可读的 ``note`` 与 ``mode``，便于前端展示"我是这样理解你的"。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# 追问标记：句尾"呢"、句首/分句处的"那/这"，以及"再/也/换成/改成/加上"等
_FOLLOWUP_PAT = re.compile(
    r"呢"
    r"|(?:^|[，,。；;、\s])(?:那|那些|这些|这(?![个些地])|上面|刚才|前面|上述)"
    r"|再|也|换(?:成|为|到)|改成|改为|调成|加上|一样|同样"
)

# 指代词：需要从上一轮结果集取值来消解
_ANAPHORA_PAT = re.compile(r"这些|那些|它们|它|上述|前述|上面|刚才|前面")

# 「按城市看 / 分国家看 / 各区域」等显式维度指示词
_DIM_INDICATOR_PAT = re.compile(r"按|分|各|每(?:个|一)?")

# 澄清接续时，若本轮含这些内容说明是新问句而非选项选择
_PICK_BLOCK_PAT = re.compile(
    r"[0-9]|上个月|本月|这个月|上个季度|上季度|本季度|去年|今年|上周|本周|今天|昨天"
    r"|环比|同比|top|排名|排行"
)

_TIME_CN = {
    "last_month": "上个月", "this_month": "本月",
    "last_quarter": "上季度", "this_quarter": "本季度",
    "last_year": "去年", "this_year": "今年",
    "last_week": "上周", "this_week": "本周",
    "today": "今天", "yesterday": "昨天",
}

_LEN_UNIT_CN = {"day": "天", "month": "个月", "year": "年"}


@dataclass
class Rewrite:
    """一次上下文改写的产物。"""

    mode: str                                   # new / followup / borrow / clarify_pick / clarify
    note: str = ""                              # 人类可读的上下文说明
    dsl: dict | None = None                     # 补全后的可执行 DSL；None 表示仍需澄清
    clarify: dict | None = None                  # 仍需澄清时的原始澄清结果
    trace: dict = field(default_factory=dict)   # 本轮识别到的 partial slot

    @property
    def resolved(self) -> bool:
        return self.dsl is not None


# ---------------- 小工具 ----------------

def _ranking_of(base: dict, metric: str) -> dict | None:
    """从上一轮 DSL 还原排序意图（仅当排序字段就是指标本身时才算 Top-N）。"""
    ob = base.get("order_by") or []
    if ob and ob[0].get("field") == metric and base.get("limit"):
        return {"direction": ob[0].get("direction", "desc"), "limit": int(base["limit"])}
    return None


def _filter_ok(f: dict, usable: set) -> bool:
    return f.get("dimension") in usable


def _usable(reg, metric: str) -> set:
    return set(reg.metrics[metric].available_dimensions)


def _dim_display(reg, d: str) -> str:
    try:
        return reg.dim_display(d)
    except Exception:
        return d


def _unsupported_dim(metric: str, trace: dict, reg) -> dict | None:
    """显式请求的维度不被该指标支持时，返回澄清（而非静默忽略后给错结果）。"""
    asked = trace.get("dimensions") or []
    if not asked:
        return None
    usable = _usable(reg, metric)
    if any(d in usable for d in asked):
        return None
    names = "、".join(_dim_display(reg, d) for d in asked)
    supported = [m for m in reg.metrics.values()
                 if set(asked) & set(m.available_dimensions)]
    options = [f"{m.name}（{m.display_name}）" for m in supported]
    msg = f"{reg.metric_display(metric)}不支持按{names}维度分析。"
    msg += "请指明你关注哪一个指标？" if options else "请换一种问法。"
    return {"action": "clarify", "message": msg, "options": options, "trace": trace}


def _is_followup(q: str) -> bool:
    return bool(_FOLLOWUP_PAT.search(q))


def _is_anaphora(q: str) -> bool:
    return bool(_ANAPHORA_PAT.search(q))


def _looks_like_pick(q: str, reg) -> bool:
    """判断本轮输入是否只是对上一轮澄清候选项的选择（而非新问句）。"""
    ql = q.lower()
    if not ql or len(ql) > 10 or _PICK_BLOCK_PAT.search(ql):
        return False
    if any(term in ql for term in reg.dim_terms):      # 带维度词 → 是新问句
        return False
    if any(term in ql for term in reg.value_terms):    # 带受控取值 → 是新问句
        return False
    return True


def _pick_option(q: str, pending: dict, reg) -> str | None:
    """在澄清候选中做最长匹配，返回选中的指标名。"""
    ql = q.lower()
    best = (0, None)
    for opt in pending.get("options") or []:
        name = str(opt).split("（")[0].split("(")[0].strip()
        m = reg.metrics.get(name)
        if m is None:
            continue
        for term in (m.name, m.display_name, *m.synonyms):
            t = str(term).strip().lower()
            if t and t in ql and len(t) > best[0]:
                best = (len(t), name)
    return best[1]


def _time_desc(spec: dict) -> str:
    if not spec:
        return "无"
    if spec.get("type") == "relative":
        v = str(spec.get("value", ""))
        if v in _TIME_CN:
            return _TIME_CN[v]
        m = re.match(r"last_(\d+)_(day|month|year)s?", v)
        if m:
            return f"近{m.group(1)}{_LEN_UNIT_CN.get(m.group(2), '')}"
        return v
    start, end = spec.get("start", ""), spec.get("end", "")
    return f"{start[:10]}~{end[:10]}"


def _describe(dsl: dict, reg) -> str:
    parts = [f"指标={reg.metric_display(dsl['metric'])}"]
    dims = dsl.get("dimensions") or []
    parts.append("维度=" + ("、".join(reg.dim_display(d) for d in dims) if dims else "无"))
    filters = dsl.get("filters") or []
    if filters:
        parts.append("筛选=" + "、".join(
            f"{reg.dim_display(f['dimension'])} {f['op']} {f['value']}" for f in filters))
    if dsl.get("time_range"):
        parts.append("时间=" + _time_desc(dsl["time_range"]))
    if dsl.get("limit"):
        parts.append(f"Top{dsl['limit']}")
    if dsl.get("compare"):
        parts.append("环比" if dsl["compare"] == "mom" else "同比")
    return "，".join(parts)


def _anaphora_filter(session, dims: list, reg) -> dict | None:
    """用上一轮成功结果集中某维度的取值，构造「指代」筛选条件。"""
    last = session.last_ok_turn() if session else None
    if last is None:
        return None
    for d in dims:
        vals = last.group_values(d)
        if vals:
            return {"dimension": d, "op": "in", "value": vals[:50]}
    return None


# ---------------- 主流程 ----------------

def rewrite(question: str, session, parser, anchor) -> Rewrite:
    """把当前问句结合会话状态改写为完整 DSL（或仍需澄清）。"""
    reg = parser.reg
    q = (question or "").strip()

    # ① 接续上一轮的澄清：把候选项选择补成完整问数
    pending = session.pending_clarify() if session else None
    if pending and _looks_like_pick(q, reg):
        picked = _pick_option(q, pending, reg)
        if picked:
            p = pending.get("partial") or {}
            dsl, _ = parser.build_dsl(picked, p.get("dimensions", []), p.get("values", []),
                                      p.get("time_range"), p.get("ranking"), p.get("compare"))
            note = (f"接续上一轮的澄清，选择指标 {reg.metric_display(picked)}；"
                    f"补全为：{_describe(dsl, reg)}")
            return Rewrite(mode="clarify_pick", note=note, dsl=dsl, trace=p)

    parsed = parser.parse(q, anchor)
    trace = parsed.get("trace") or {}
    base = session.context_dsl() if session else None

    # ② 解析成功 + 追问标记 → 显式槽位覆盖、其余继承上一轮
    if parsed["action"] == "dsl":
        if base and _is_followup(q):
            return _merge(base, parsed, trace, q, parser, session)
        return Rewrite(mode="new", dsl=parsed["dsl"], trace=trace)

    # ③ 解析不出指标，但识别到了其它槽位 → 指标沿用上一轮（省略主语）
    if base and _borrowable(trace, q):
        return _borrow(base, trace, q, parser, session)

    # ④ 仍需澄清
    return Rewrite(mode="clarify", clarify=parsed, trace=trace)


def _borrowable(trace: dict, q: str) -> bool:
    """能否从上下文借到指标。

    三个条件缺一不可：
    1. 本轮不存在指标歧义（否则应先澄清"究竟是哪个指标"）；
    2. 至少识别到一个其它槽位（维度/取值/时间/排序/同环比）；
    3. 句子里有"追问/维度指示"这类**省略标记**——否则像"今天天气怎么样"
       这种恰好含时间词的全新问题会被误补成销售查询（上下文污染）。
    """
    if len(trace.get("metric_candidates") or []) > 1:
        return False
    if not any(trace.get(k) for k in ("dimensions", "values", "time_range", "ranking", "compare")):
        return False
    return _is_followup(q) or bool(_DIM_INDICATOR_PAT.search(q))


def _borrow(base: dict, trace: dict, q: str, parser, session) -> Rewrite:
    """省略指标：指标沿用上一轮，其余槽位取本轮显式值，缺失部分继承上一轮。"""
    reg = parser.reg
    metric = base["metric"]
    usable = _usable(reg, metric)

    blocked = _unsupported_dim(metric, trace, reg)
    if blocked:
        return Rewrite(mode="clarify", clarify=blocked, trace=trace,
                       note=f"本轮显式要求的维度不被 {reg.metric_display(metric)} 支持，改为澄清")

    dims = [d for d in trace.get("dimensions", []) if d in usable]
    inherited_dims = False
    if not dims:
        dims = [d for d in base.get("dimensions", []) if d in usable]
        inherited_dims = True

    values = trace.get("values") or []
    time_range = trace.get("time_range") or base.get("time_range")
    ranking = trace.get("ranking") or _ranking_of(base, metric)
    compare = trace.get("compare")

    dsl, _ = parser.build_dsl(metric, dims, values, time_range, ranking, compare)
    if not values:
        dsl["filters"] = [f for f in base.get("filters", []) if _filter_ok(f, usable)]

    note = f"省略了指标，沿用上一轮的 {reg.metric_display(metric)}"
    if inherited_dims:
        note += "，维度也一并继承"
    note += f"；补全为：{_describe(dsl, reg)}"
    return Rewrite(mode="borrow", note=note, dsl=_apply_anaphora(dsl, q, session, reg),
                   trace=trace)


def _merge(base: dict, parsed: dict, trace: dict, q: str, parser, session) -> Rewrite:
    """追问：本轮显式给出的槽位覆盖上一轮，未提及的槽位继承上一轮。"""
    reg = parser.reg
    metric = parsed["dsl"]["metric"]
    usable = _usable(reg, metric)
    inherited, overridden = [], []

    blocked = _unsupported_dim(metric, trace, reg)
    if blocked:
        return Rewrite(mode="clarify", clarify=blocked, trace=trace,
                       note=f"本轮显式要求的维度不被 {reg.metric_display(metric)} 支持，改为澄清")

    explicit_dims = [d for d in trace.get("dimensions", []) if d in usable]
    if explicit_dims:
        dims, overridden = explicit_dims, overridden + ["维度"]
    elif parsed["dsl"].get("dimensions") and _DIM_INDICATOR_PAT.search(q):
        dims, overridden = parsed["dsl"]["dimensions"], overridden + ["维度"]
    else:
        dims = [d for d in base.get("dimensions", []) if d in usable]
        if dims:
            inherited.append("维度")

    values = trace.get("values") or []
    time_range = trace.get("time_range")
    if time_range is None and not trace.get("compare"):
        time_range = base.get("time_range")
        if time_range:
            inherited.append("时间")
    elif time_range:
        overridden.append("时间")

    ranking = trace.get("ranking") or _ranking_of(base, metric)
    if trace.get("ranking"):
        overridden.append("排序")
    elif ranking:
        inherited.append("排序")

    if metric != base.get("metric"):
        overridden.append("指标")

    dsl, _ = parser.build_dsl(metric, dims, values, time_range, ranking, trace.get("compare"))
    if not values:
        carried = [f for f in base.get("filters", []) if _filter_ok(f, usable)]
        if carried:
            inherited.append("筛选")
        dsl["filters"] = carried

    note_parts = []
    if inherited:
        note_parts.append("沿用上一轮的" + "、".join(inherited))
    if overridden:
        note_parts.append("本轮替换" + "、".join(overridden))
    note = "追问：" + ("；".join(note_parts) if note_parts else "沿用上一轮全部槽位")
    note += f"。解析为：{_describe(dsl, reg)}"
    return Rewrite(mode="followup", note=note, dsl=_apply_anaphora(dsl, q, session, reg),
                   trace=trace)


def _apply_anaphora(dsl: dict, q: str, session, reg) -> dict:
    """指代消解：用上一轮结果集里的取值给 dsl 加筛选条件。"""
    if not session or not q or not _is_anaphora(q):
        return dsl
    dims = list(dsl.get("dimensions") or []) or [f["dimension"] for f in dsl.get("filters") or []]
    ana = _anaphora_filter(session, dims, reg)
    if not ana:
        return dsl
    dsl["filters"] = [f for f in dsl.get("filters", []) if f["dimension"] != ana["dimension"]]
    dsl["filters"].append(ana)
    return dsl
