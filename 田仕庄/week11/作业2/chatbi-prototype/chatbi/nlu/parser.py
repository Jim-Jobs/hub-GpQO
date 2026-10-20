"""规则式 NL→DSL 解析器：基于受控词表匹配，离线可用（作为 LLM 之外的基线与兜底）。"""
from __future__ import annotations

import re
from datetime import date

from ..timeres import _add_months, _month_start

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _num(s: str):
    if s.isdigit():
        return int(s)
    return _CN_NUM.get(s)


def _rel(value: str) -> dict:
    return {"type": "relative", "value": value}


def _abs_month(y: int, mo: int) -> dict:
    start = date(y, mo, 1)
    return {"type": "absolute", "start": start.isoformat(),
            "end": _add_months(start, 1).isoformat()}


def _abs_year(y: int) -> dict:
    return {"type": "absolute", "start": date(y, 1, 1).isoformat(),
            "end": date(y + 1, 1, 1).isoformat()}


# 顺序即优先级：先长跨度，后绝对时间，再相对时间
_TIME_PATTERNS = [
    (re.compile(r"(?:近|最近|过去)\s*(\d+)\s*个月"), lambda m, a: _rel(f"last_{m.group(1)}_months")),
    (re.compile(r"(?:近|最近|过去)\s*(\d+)\s*天"), lambda m, a: _rel(f"last_{m.group(1)}_days")),
    (re.compile(r"(?:近|最近|过去)\s*(\d+)\s*年"), lambda m, a: _rel(f"last_{m.group(1)}_years")),
    (re.compile(r"(?:近|最近)\s*半年"), lambda m, a: _rel("last_6_months")),
    (re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月"), lambda m, a: _abs_month(int(m.group(1)), int(m.group(2)))),
    (re.compile(r"(\d{4})\s*年"), lambda m, a: _abs_year(int(m.group(1)))),
    (re.compile(r"(?<!\d)(\d{1,2})\s*月份?"), lambda m, a: _abs_month(a.year, int(m.group(1)))),
    (re.compile(r"上个月|上月"), lambda m, a: _rel("last_month")),
    (re.compile(r"本月|这个月|当月"), lambda m, a: _rel("this_month")),
    (re.compile(r"上个季度|上季度"), lambda m, a: _rel("last_quarter")),
    (re.compile(r"本季度|这个季度|当季"), lambda m, a: _rel("this_quarter")),
    (re.compile(r"去年|上一年|去年度"), lambda m, a: _rel("last_year")),
    (re.compile(r"今年|本年度|本年|当年"), lambda m, a: _rel("this_year")),
    (re.compile(r"上周|上个星期"), lambda m, a: _rel("last_week")),
    (re.compile(r"本周|这周|这个星期"), lambda m, a: _rel("this_week")),
    (re.compile(r"今天|今日|当天"), lambda m, a: _rel("today")),
    (re.compile(r"昨天|昨日"), lambda m, a: _rel("yesterday")),
]

_RANK_PAT = re.compile(r"(?:top|前|排名前|排行前)\s*(\d+|[一二两三四五六七八九十])", re.IGNORECASE)
_RANK_DESC = re.compile(r"最高|最多|最大|最好|最畅销|最热|排名|排行|top|最受欢迎")
_RANK_ASC = re.compile(r"最低|最少|最小|最差|最冷")


class RuleParser:
    """基于受控词表的规则解析器。"""

    def __init__(self, registry):
        self.reg = registry

    # ---------- 各 Slot 识别 ----------

    def _match_metric(self, ql: str):
        best = {}
        for term, metric in self.reg.metric_terms.items():
            if term in ql:
                best[metric] = max(best.get(metric, 0), len(term))
        if not best:
            return None, []
        top = max(best.values())
        winners = sorted(m for m, ln in best.items() if ln == top)
        return (winners[0] if len(winners) == 1 else None), winners

    def _match_dims(self, ql: str):
        hits = []
        for term, dim in self.reg.dim_terms.items():
            idx = ql.find(term)
            if idx >= 0:
                hits.append((idx, -len(term), dim))
        hits.sort()
        out = []
        for _idx, _neg, d in hits:
            if d not in out:
                out.append(d)
        return out

    def _match_values(self, q: str):
        """最长匹配优先 + 位置占位，避免 "Heavy Metal" 被 "Metal" 重复命中。"""
        ql = q.lower()
        consumed = []
        found = []
        terms = sorted(self.reg.value_terms.items(), key=lambda kv: -len(kv[0]))
        for term, bucket in terms:
            start = ql.find(term)
            while start != -1:
                end = start + len(term)
                if not any(not (end <= cs or start >= ce) for cs, ce in consumed):
                    consumed.append((start, end))
                    for (dim, canon) in bucket:
                        if (dim, canon) not in found:
                            found.append((dim, canon))
                    break
                start = ql.find(term, start + 1)
        return found

    def _match_time(self, q: str, anchor: date):
        for pat, handler in _TIME_PATTERNS:
            m = pat.search(q)
            if m:
                return handler(m, anchor)
        return None

    def _match_ranking(self, q: str):
        m = _RANK_PAT.search(q)
        limit = _num(m.group(1)) if m else None
        direction = "desc"
        if _RANK_ASC.search(q) and not _RANK_DESC.search(q):
            direction = "asc"
        elif _RANK_DESC.search(q) or limit is not None:
            direction = "desc"
        else:
            return None
        return {"direction": direction, "limit": limit or 10}

    def _match_compare(self, q: str):
        if re.search(r"环比", q):
            return "mom"
        if re.search(r"同比", q):
            return "yoy"
        return None

    # ---------- 主流程 ----------

    def build_dsl(self, metric: str, dims_all: list, raw_values: list,
                  time_range: dict | None, ranking: dict | None,
                  compare: str | None) -> tuple[dict, list]:
        """按识别出的 slot 组装受控 DSL。

        返回 (dsl, 被忽略的字段列表)。抽成独立方法是为了让「多轮上下文补全」
        能直接用同一套组装规则，避免两处逻辑漂移。
        """
        usable = set(self.reg.metrics[metric].available_dimensions)
        ignored = [d for d in dims_all if d not in usable]
        dims = [d for d in dims_all if d in usable]

        by_dim = {}
        for dim, canon in raw_values:
            if dim not in usable:
                ignored.append(dim)
                continue
            if canon not in by_dim.setdefault(dim, []):
                by_dim[dim].append(canon)

        filters = []
        for dim, vals in by_dim.items():
            if len(vals) > 1:
                filters.append({"dimension": dim, "op": "in", "value": vals})
                if dim not in dims:
                    dims.append(dim)
            else:
                filters.append({"dimension": dim, "op": "eq", "value": vals[0]})

        if compare and not time_range:
            time_range = _rel("last_month") if compare == "mom" else _rel("this_year")

        dsl = {"metric": metric, "dimensions": dims, "filters": filters}
        if time_range:
            dsl["time_range"] = time_range
        if ranking:
            dsl["order_by"] = [{"field": metric, "direction": ranking["direction"]}]
            dsl["limit"] = ranking["limit"]
        elif dims:
            time_dims = [d for d in dims if self.reg.is_time_dim(d)]
            if time_dims:
                dsl["order_by"] = [{"field": time_dims[0], "direction": "asc"}]
        if compare:
            dsl["compare"] = compare
        return dsl, sorted(set(ignored))

    def parse(self, question: str, anchor: date) -> dict:
        """完整解析；无法确定指标时返回澄清，并附带已识别的 partial trace。

        无论成功或澄清都返回 ``trace``，其中 ``metric_candidates`` 为歧义时的候选指标；
        澄清时 trace 仍携带已识别的维度/取值/时间，供多轮上下文补全使用。
        """
        q = (question or "").strip()
        ql = q.lower()

        metric, winners = self._match_metric(ql)
        matched_dims = self._match_dims(ql)
        matched_values = self._match_values(q)
        matched_time = self._match_time(q, anchor)
        matched_ranking = self._match_ranking(q)
        matched_compare = self._match_compare(q)

        trace = {
            "metric": metric,
            "metric_candidates": winners,
            "dimensions": matched_dims,
            "values": matched_values,
            "time_range": matched_time,
            "ranking": matched_ranking,
            "compare": matched_compare,
            "ignored": [],
        }

        if metric is None:
            if len(winners) > 1:
                return {"action": "clarify", "trace": trace,
                        "message": "识别到多个指标，一次问数只支持一个，请指明你关注哪一个？",
                        "options": [f"{w}（{self.reg.metric_display(w)}）" for w in winners]}
            return {"action": "clarify", "trace": trace,
                    "message": "没有识别到可查询的指标，请换一种问法。",
                    "options": [f"{m.name}（{m.display_name}）" for m in self.reg.metrics.values()]}

        dsl, ignored = self.build_dsl(metric, matched_dims, matched_values,
                                      matched_time, matched_ranking, matched_compare)
        usable = set(self.reg.metrics[metric].available_dimensions)
        trace["dimensions"] = [d for d in matched_dims if d in usable]
        trace["ignored"] = ignored
        return {"action": "dsl", "dsl": dsl, "trace": trace}
