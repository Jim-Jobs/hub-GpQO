#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
中标金额与中标供应商解析器

背景: ccgp 公告由全国数千家代理机构各自套模板生成, 同一字段写法高度不一致。
实测金额至少存在五种形态:
  1) 表格形态: 表头 "中标金额" 列 + 数据行纯数字 (675000)          -> 按列索引取值
  2) 单位前置: "成交金额(万元)：27"                                -> 单位在标签括号内
  3) 单位后置: "中标（成交）金额：220.0000000（万元）"             -> 单位在数值括号内
  4) 宽松文本: "金额： （可填写下浮率…）标包【1】29.85万元"       -> 标签与数值间有模板噪声
  5) 干扰金额: 正文另有"代理服务费 0.44775 万元"                   -> 需上下文排除

策略:
  金额 = 表格列定位(最可信) -> 标签锚定 + 邻域取单位(次可信)
  供应商 = 表格列定位 -> 文本正则
统一做单位归一(万元->元) 与量级校验。
"""
from __future__ import annotations

import re

CELL_SEP = "||"

AMOUNT_HEADERS = (
    "中标金额", "成交金额", "中标（成交）金额", "中标(成交)金额",
    "中标（成交金额）", "中标(成交金额)", "中标/成交金额", "中标价",
    "成交价", "中标（成交）价", "中标(成交)价", "合同金额", "中标金额（元）",
)

SUPPLIER_HEADERS = (
    "供应商名称", "中标供应商", "成交供应商", "中标（成交）供应商",
    "中标供应商名称", "成交供应商名称", "中标人", "成交人",
    "中标单位", "成交单位", "中标人名称", "成交人名称",
)

# 强干扰: 出现在标签前 60 字内则整段作废 (这些指向"别的钱")
STRONG_EXCLUDE = (
    "代理服务费", "代理费", "中标服务费", "采购代理", "预算金额", "预算价",
    "最高限价", "控制价", "保证金", "评审专家", "附件",
)
# 弱干扰: 仅当紧贴数值前 6 字时才作废。模板提示语(如"可填写下浮率、折扣率")离数值较远,
# 按强规则处理会误杀真实金额。
WEAK_EXCLUDE = ("下浮率", "费率", "折扣率", "优惠率", "单价", "得分", "数量")

# 标签锚点。真实公告里标签与数值之间常夹模板噪声, 冒号也可能缺失:
#   "成交金额(万元)：27"                       内嵌单位 + 冒号
#   "中标（成交）金额（元）\（%）：2530000.00"   标签后还有 （元）\（%） 噪声
#   "成交金额 || 2,000,000.00"                 表格压行后以 || 分隔, 无冒号
# 因此单位括号后允许一小段非数字噪声, 冒号改为可选。
LABEL_RE = re.compile(
    r"(?:中标\s*[（(]?\s*成交\s*[）)]?\s*金额"
    r"|中标金额|成交金额|合同金额"
    r"|中标\s*[（(]?\s*成交\s*[）)]?\s*价"
    r"|中标价|成交价)"
    r"\s*(?:[（(]\s*(万元|万|元)\s*[）)])?"
    r"[^0-9：:\n]{0,14}"
    r"[：:]?\s*"
)

NUM_RE = re.compile(r"([0-9][0-9,，]*(?:\.[0-9]+)?)")
UNIT_RE = re.compile(r"(万元|万|元)")


def _to_yuan(num: str, unit: str) -> float | None:
    try:
        v = float(num.replace(",", "").replace("，", ""))
    except ValueError:
        return None
    if unit in ("万元", "万"):
        v *= 10000
    return v


def _plausible(v: float) -> bool:
    """ESG/双碳咨询与平台类项目实际中标价区间, 放宽到 1 千 ~ 50 亿。"""
    return 1000 <= v <= 5e9


def _rows(text: str) -> list[list[str]]:
    return [[c.strip() for c in line.split(CELL_SEP)] if CELL_SEP in line else [line.strip()]
            for line in text.split("\n")]


def _is_amount_header(h: str) -> bool:
    if h in AMOUNT_HEADERS:
        return True
    if not h or len(h) > 24:
        return False
    return ("金额" in h or "价" in h) and any(k in h for k in ("中标", "成交", "合同")) \
        and "服务费" not in h and "代理" not in h


def _is_supplier_header(h: str) -> bool:
    if h in SUPPLIER_HEADERS:
        return True
    if not h or len(h) > 24:
        return False
    if any(k in h for k in ("地址", "编码", "代码", "电话", "联系人", "得分", "评分")):
        return False
    return any(k in h for k in ("供应商", "中标人", "成交人", "中标单位", "成交单位")) \
        and ("名称" in h or len(h) <= 8)


def _from_table(rows: list[list[str]], matcher) -> tuple[str | None, str]:
    """按表头定位列索引, 取紧邻数据行的同列单元格。"""
    for i, cells in enumerate(rows):
        if len(cells) < 2:
            continue
        for j, h in enumerate(cells):
            if not matcher(h):
                continue
            for k in range(i + 1, min(i + 4, len(rows))):
                nxt = rows[k]
                if len(nxt) != len(cells):
                    continue
                val = (nxt[j] or "").strip()
                if val and not matcher(val):
                    return val, f"table[{h}]"
    return None, ""


def _clean_supplier(s: str) -> str:
    s = re.sub(r"^标包\s*[【\[\(]?\s*\d+\s*[】\]\)]?\s*", "", s)
    s = re.sub(r"^[（(]?\s*第?[一二三四五六七八九十\d]+\s*(?:标段|标包|包)\s*[）)]?\s*", "", s)
    s = re.sub(r"^[：:\s]+", "", s)
    s = re.sub(r"[\s\u3000]+", "", s)
    return s.strip("：:，,。;；|")[:80]


def _unit_after(seg: str, pos: int) -> str:
    """取紧邻数值的单位。

    必须在遇到下一个数字之前找到单位, 否则会误判:
    如 "标包【1】29.85万元" 中, 若从 "1" 往后找, 会跨过 29.85 找到 "万元",
    把序号 1 误读成 1 万元。
    """
    tail = seg[pos: pos + 12]
    nxt = NUM_RE.search(tail)
    frag = tail[: nxt.start()] if nxt else tail
    um = UNIT_RE.search(frag)
    return um.group(1) if um else ""


def _parse_amount_text(text: str) -> tuple[float | None, str, str]:
    """标签锚定 -> 数值邻域取单位。"""
    best = None
    for m in LABEL_RE.finditer(text):
        pre_unit = m.group(1) or ""
        pre60 = text[max(0, m.start() - 60): m.start()]
        if any(x in pre60 for x in STRONG_EXCLUDE):
            continue

        seg = text[m.end(): m.end() + 48]

        # 收集段内数字候选。注意不能用"最后一个"覆盖前面的:
        # 表格压行后 seg 里常跟着"联系方式 / 电话：0731-85387499",
        # 若取最后一个数字会把电话号码当成成交金额(8538 万 vs 真实 200 万)。
        cands: list[tuple[float, str, str]] = []
        for nm in list(NUM_RE.finditer(seg[:56]))[:5]:
            pre6 = seg[max(0, nm.start() - 6): nm.start()]
            if any(x in pre6 for x in WEAK_EXCLUDE):
                continue
            unit = _unit_after(seg, nm.end()) or pre_unit
            v = _to_yuan(nm.group(1), unit)
            if v is None:
                continue
            scaled = False
            if not unit and v < 1000:
                v *= 10000  # 无单位且数值过小, 按万元兜底
                scaled = True
            if not _plausible(v):
                continue
            # "包1" 之类序号被兜底放大成 1 万元, 丢弃
            if scaled and v <= 20000:
                continue
            cands.append((v, unit, nm.group(0)))
        # 有明确单位的优先; 同级别取最靠近标签的第一个
        pick = next((c for c in cands if c[1]), None) or (cands[0] if cands else None)
        if not pick:
            continue

        v, unit, _num = pick
        # 标签越靠后(正文结论区)越可信, 同时优先有明确单位的
        score = (1.0 if unit else 0.9) + m.start() / 1e7
        if best is None or score > best[0]:
            best = (score, v, text[m.start(): m.end() + 45].replace("\n", " ").strip()[:80],
                    "label" + ("/unit" if unit else "/nounit"))
    if best:
        return round(best[1], 2), best[2], best[3]
    return None, "", ""


def extract(text: str) -> dict:
    """从公告正文提取中标金额与中标供应商。"""
    res = {
        "amount_yuan": None, "amount_raw": "", "amount_method": "",
        "supplier": "", "supplier_method": "",
    }
    if not text:
        return res

    rows = _rows(text)

    # ---- 金额: 先表格列, 再文本标签
    raw, method = _from_table(rows, _is_amount_header)
    if raw:
        nm = NUM_RE.search(raw)
        if nm:
            unit = _unit_after(raw, nm.end())
            v = _to_yuan(nm.group(1), unit)
            if v and not unit and v < 1000:
                v *= 10000
            if v and _plausible(v):
                res.update(amount_yuan=round(v, 2), amount_raw=raw[:60],
                           amount_method=method + "/col")

    if res["amount_yuan"] is None:
        v, raw_t, meth = _parse_amount_text(text)
        if v:
            res.update(amount_yuan=v, amount_raw=raw_t, amount_method=meth)

    # ---- 供应商: 先表格列, 再文本
    sup, method = _from_table(rows, _is_supplier_header)
    if sup:
        cand = _clean_supplier(sup)
        if cand and not re.search(r"(供应商|中标人|成交人)$", cand):
            res["supplier"] = cand
            res["supplier_method"] = method

    if not res["supplier"]:
        for pat in (
            r"(?:中标|成交)\s*[（(]?\s*成交\s*[）)]?\s*供应商(?:名称)?\s*[：:]\s*([^\n|]{2,60})",
            r"供应商名称\s*[：:]\s*([^\n|]{2,60})",
            r"(?:中标人|成交人|中标单位|成交单位)(?:名称)?\s*[：:]\s*([^\n|]{2,60})",
        ):
            m = re.search(pat, text)
            if m:
                cand = _clean_supplier(m.group(1))
                if cand and len(cand) >= 4 and not re.search(r"(名称|地址|编码|得分|金额|联系|无$)", cand):
                    res["supplier"] = cand
                    res["supplier_method"] = "text"
                    break

    return res
