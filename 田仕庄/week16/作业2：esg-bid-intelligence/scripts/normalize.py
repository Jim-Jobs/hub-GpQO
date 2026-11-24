#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
清洗 / 打标 / 项目聚合

三件关键事:
  1) 去噪: 标题里出现"双碳"不代表就是双碳项目。典型陷阱是楼宇名称
     —— "中国石油大学'双碳'创新国际大厦(实验楼)项目全过程咨询" 实质是基建工程,
     若不清洗会把高校盖楼的钱算进 ESG 市场规模, 严重高估。
  2) 分层: L1 ESG核心 / L2 双碳 / L3 绿色周边, 并额外给出 relevance(高/中/低),
     让报告能区分"真金白银的核心盘"和"泛低碳外延"。
  3) 聚合: 同一项目常有 招标公告 + 更正公告 + 中标公告 多条, 必须按
     采购人+项目名聚合成一条项目记录, 否则成交金额会被重复统计。

用法:
  python normalize.py --raw ./esg_bid_data/raw/notices_x.json --out ./esg_bid_data
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from amount_parser import extract as extract_amount  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REF = Path(__file__).parent.parent / "references" / "taxonomy.json"

# 基建/货物类硬信号: 命中即判工程/采购, 不接受"咨询"字样的救援。
# 典型陷阱: "…大厦项目造价咨询服务" 虽是"咨询", 却是工程造价, 与 ESG 咨询无关;
# "…人才培养实训平台" 是设备采购, 不是咨询服务。
HARD_ENGINEERING = (
    "大厦", "实验楼", "科技楼", "教学楼", "办公楼", "宿舍", "场馆", "展厅", "展陈",
    "基坑", "桩基", "土建", "施工", "装修", "安装工程", "监理", "勘察",
    "造价咨询", "工程造价", "全过程项目管理", "工程量清单", "招标清单", "控制价编制",
    "资产购置", "购置", "实训", "实验基地", "设备采购", "仪器", "家具",
    "租赁", "物业", "食堂", "绿化工程", "市政", "暖通", "消防设施",
    "改造工程", "总承包", "供货",
    # 大额工程/货物类: 这些动辄数千万, 混进核心样本会严重高估市场规模。
    # 典型: 零碳校园供能、能源费用托管、大科学装置、实验室条件建设、公交车更新、
    #       "监测系统工程货物采购"、亚投行贷款"全过程咨询服务"(工程咨询, 非 ESG 咨询)。
    "供能", "分布式能源", "能源费用托管", "费用托管", "合同能源管理",
    "大科学装置", "科学装置", "中试平台", "实验采购", "可靠性实验",
    "实验室条件建设", "货物采购", "采购及安装", "公交车更新", "车辆更新",
    "基础设施示范", "环境检测", "试验室采购", "台架采购",
    "房屋修缮", "修缮项目", "钻探",
)
# 服务类硬信号: 明确指向 ESG/碳/研究/软件类标的
HARD_SERVICE = (
    "ESG", "碳核查", "碳盘查", "温室气体", "碳足迹", "碳资产", "碳交易", "碳标签",
    "双碳规划", "碳达峰", "碳中和", "研究报告", "指标体系", "平台建设", "平台开发",
    "系统建设", "数字化", "数据库", "课题", "技术服务", "能力提升", "能力建设",
    "方案编制", "规划编制", "咨询服务", "顾问", "监管平台", "管理平台", "信息系统",
    "软件开发", "方法学",
)
# 出现这些词说明标的物仍是工程/货物, 即使同时含"咨询服务"也判工程
# 命中这些词时即使同时有"温室气体/碳中和/系统"等服务词, 仍判工程。
# 否则 "…监测系统工程货物采购项目" 会因正文含"系统"被兜底正则救成服务。
ENG_OVERRIDE = (
    "造价", "项目管理", "工程量清单", "招标清单", "资产购置", "实训", "设备", "租赁", "购置",
    "供能", "能源费用托管", "费用托管", "合同能源管理", "大科学装置", "科学装置",
    "中试平台", "实验采购", "可靠性实验", "实验室条件建设", "货物采购",
    "采购及安装", "公交车更新", "车辆更新", "基础设施示范", "环境检测",
    "试验室采购", "台架采购", "房屋修缮", "修缮项目", "钻探",
)
# 会议/培训/活动类: 属于 ESG 生态但不是咨询服务, 单独剔除以免污染单价基准
ACTIVITY_SIGNALS = (
    "大会", "论坛", "年会", "峰会", "研讨会", "活动", "展位", "展览", "博览会",
    "研修班", "培训班", "宣讲", "路演", "沙龙", "宣传", "推广",
)
# 软性修饰: 关键词只是背景, 不是标的本身
SOFT_QUALIFIERS = ("背景下", "目标下", "引领下", "约束下", "视角下", "形势下", "战略下", "驱动下")


def load_taxonomy() -> dict:
    return json.loads(REF.read_text(encoding="utf-8"))


def strip_agent_prefix(title: str) -> str:
    """剥掉 ccgp 标题里的代理机构/采购人前缀。

    站内公告常见格式: 「XX工程造价咨询有限公司关于YY双碳规划编制项目中标公告」。
    代理机构名里的「造价咨询 / 项目管理 / 工程咨询」会误触发工程类判定,
    把真实的双碳规划研究错误剔除。故先切掉「…关于」之前的部分。
    """
    for m in re.finditer(r"关于", title):
        head = title[: m.start()]
        if 4 <= len(head) <= 55 and re.search(
                r"(公司|中心|事务所|集团|院|局|委员会|管理处|管委会)$", head):
            return title[m.end():].strip()
    return title


def clean_display_title(title: str) -> str:
    """项目展示名: 去掉代理机构前缀与尾部的公告动作词, 让清单可直接阅读。"""
    t = strip_agent_prefix(title)
    for _ in range(2):
        t = re.sub(r"[（(【\[][^）)】\]]{0,30}[）)】\]]\s*$", "", t)
        t = re.sub(r"(中标|成交|结果|招标|采购|竞争性磋商|竞争性谈判|询价|单一来源|"
                   r"更正|变更|废标|终止|流标|失败|公告|公示|通知|公开|邀请|"
                   r"遴选|比选|二次|重新|第[一二三四五六七八九十\d]+次)+\s*$", "", t)
    return t.strip(" 的之-—_、，,。：:；;")


def classify(title: str, tax: dict) -> tuple[str, list[str], str]:
    """返回 (主分类, 全部命中分类, 相关性等级)。"""
    hits: list[tuple[str, str]] = []
    for tier, cfg in tax["tiers"].items():
        for pat in cfg["patterns"]:
            if re.search(pat, title, re.I):
                hits.append((tier, pat))
                break

    tiers = [t for t, _ in hits]
    if not tiers:
        return "", [], "low"

    # 主分类取最高层级 (L1 优先)
    order = list(tax["tiers"].keys())
    primary = sorted(tiers, key=lambda t: order.index(t))[0]

    if any(q in title for q in SOFT_QUALIFIERS):
        relevance = "medium"
    elif primary == "L3_green":
        # 宽口径: 绿色低碳周边也计入统计。但只在标题里泛泛出现"绿色低碳"
        # 而无具体服务标的的, 仍视为低相关, 排除出核心样本。
        relevance = "medium" if re.search(
            r"(绿色工厂|绿色供应链|绿色制造|节能诊断|节能评估|能源审计|清洁生产|"
            r"循环经济|绿色金融|转型金融|气候投融资|气候风险|TCFD|ISSB|"
            r"咨询|服务|评价|评估|认证|诊断|审计|编制|规划|研究|核查|技术)",
            title, re.I) else "low"
    else:
        relevance = "high"

    return primary, tiers, relevance


def detect_nature(title: str) -> str:
    """项目性质: service / engineering / activity。

    判定顺序很重要: 先看是否会议培训, 再看是否有基建/货物硬信号。
    若"工程信号 + 服务信号"同时出现(如"大厦项目造价咨询服务"), 以标的物为准 ——
    含造价/项目管理/资产购置/实训/设备等词的仍判工程, 避免被"咨询"二字误导。
    """
    title = strip_agent_prefix(title)
    if any(s in title for s in ACTIVITY_SIGNALS):
        return "activity"

    has_eng = any(s in title for s in HARD_ENGINEERING)
    has_svc = any(s in title for s in HARD_SERVICE)

    if has_eng and has_svc:
        if any(s in title for s in ENG_OVERRIDE):
            return "engineering"
        # 无 override 词时, 看是否落在软件/研究/评价类表述
        return "service" if re.search(
            r"(平台建设|平台开发|系统|软件|数字化|指标体系|研究报告|评价|评估|核查|盘查|规划|方案|标准)",
            title) else "engineering"
    if has_eng:
        return "engineering"
    return "service"


def infer_industry(buyer: str, tax: dict) -> str:
    cfg = tax["buyer_industry"]
    for rule in cfg["rules"]:
        if any(p in buyer for p in rule["patterns"]):
            return rule["industry"]
    return cfg["fallback"]


def norm_project_key(buyer: str, title: str) -> str:
    """项目聚合键: 同一项目的"招标公告/更正公告/中标公告"必须落到同一 key。

    先剥离括号补充(如"（二次）""（成交）"), 再剥离尾部动作词, 最后去标点。
    顺序很关键: 若先剥动作词, "…项目的中标（成交）结果公告" 因尾部是"公告"以外的
    括号结构而剥离失败, 导致同一项目被拆成多条。
    """
    t = strip_agent_prefix(title)
    t = re.sub(r"[（(【\[][^）)】\]]{0,30}[）)】\]]", "", t)
    t = re.sub(r"(中标|成交|结果|招标|采购|竞争性磋商|竞争性谈判|询价|单一来源|"
               r"更正|变更|废标|终止|流标|失败|公告|公示|通知|公开|邀请|"
               r"遴选|比选|磋商|谈判|二次|重新|第[一二三四五六七八九十\d]+次)+", "", t)
    t = re.sub(r"[的之]$", "", t.strip())
    t = re.sub(r"[\s\-—_、，,。：:；;\"'《》]", "", t)
    b = re.sub(r"(本级|机关|有限公司|股份有限公司|集团|管理委员会|管委会)", "", buyer)
    return (b[:12] + "|" + t[:60]) if t else ""


def normalize(raw_path: Path, outdir: Path, tax: dict) -> tuple[list, list]:
    notices = json.loads(raw_path.read_text(encoding="utf-8"))
    noise = tax["noise_patterns"]
    status_map = tax["status_map"]

    clean = []
    for n in notices:
        title = strip_agent_prefix(n.get("title", "") or "")
        if not title:
            continue
        if any(re.search(p, title) for p in noise):
            continue

        tier, tiers, relevance = classify(title, tax)
        if not tier:
            continue

        nature = detect_nature(title)
        ntype = n.get("notice_type", "")
        status = "other"
        for k, v in status_map.items():
            if k in ntype:
                status = v
                break

        amt_info = extract_amount(n.get("detail_text", "")) if n.get("detail_text") else {
            "amount_yuan": None, "amount_raw": "", "amount_method": "",
            "supplier": "", "supplier_method": "",
        }

        buyer = n.get("buyer", "")
        pub = n.get("pub_date", "")
        year = pub[:4] if re.match(r"^\d{4}", pub) else ""
        month = pub[5:7] if len(pub) >= 7 else ""

        clean.append({
            **{k: n.get(k, "") for k in (
                "id", "summary", "pub_date", "buyer", "agency",
                "notice_type", "region", "detail_url")},
            "title": title,
            "year": year,
            "month": month,
            "status": status,
            "tier": tier,
            "tiers": tiers,
            "relevance": relevance,
            "nature": nature,
            "industry": infer_industry(buyer, tax),
            "amount_yuan": amt_info["amount_yuan"],
            "amount_raw": amt_info["amount_raw"],
            "amount_method": amt_info["amount_method"],
            "supplier": amt_info["supplier"],
            "keywords": n.get("keywords") or ([n.get("keyword")] if n.get("keyword") else []),
            "project_key": norm_project_key(buyer, title),
        })

    # ---- 项目级聚合
    groups: dict[str, list[dict]] = defaultdict(list)
    for c in clean:
        groups[c["project_key"] or c["id"]].append(c)

    projects = []
    for key, rows in groups.items():
        awards = [r for r in rows if r["status"] == "awarded"]
        base = awards[0] if awards else rows[0]
        # 金额优先取有值的成交公告
        amt = next((r["amount_yuan"] for r in awards if r["amount_yuan"]), None)
        if amt is None:
            amt = next((r["amount_yuan"] for r in rows if r["amount_yuan"]), None)
        winner = next((r["supplier"] for r in awards if r["supplier"]), "")

        dates = sorted(r["pub_date"] for r in rows if r["pub_date"])
        projects.append({
            "project_key": key,
            "project_name": clean_display_title(base["title"]),
            "buyer": base["buyer"],
            "industry": base["industry"],
            "region": base["region"],
            "tier": base["tier"],
            "tiers": base["tiers"],
            "relevance": base["relevance"],
            "nature": base["nature"],
            "status": "awarded" if awards else base["status"],
            "first_date": dates[0] if dates else "",
            "award_date": awards[0]["pub_date"] if awards else "",
            "year": (awards[0]["pub_date"][:4] if awards else (dates[0][:4] if dates else "")),
            "amount_yuan": amt,
            "winner": winner,
            "n_notices": len(rows),
            "notice_ids": [r["id"] for r in rows],
            "detail_url": base["detail_url"],
        })

    projects.sort(key=lambda p: (p["award_date"] or p["first_date"]), reverse=True)
    return clean, projects


def main():
    ap = argparse.ArgumentParser(description="清洗打标与项目聚合")
    ap.add_argument("--raw", required=True, help="crawler 输出的 notices_*.json")
    ap.add_argument("--out", default="./esg_bid_data")
    args = ap.parse_args()

    outdir = Path(args.out).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    tax = load_taxonomy()

    clean, projects = normalize(Path(args.raw), outdir, tax)

    (outdir / "notices_clean.json").write_text(
        json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
    (outdir / "projects.json").write_text(
        json.dumps(projects, ensure_ascii=False, indent=1), encoding="utf-8")

    awarded = [p for p in projects if p["status"] == "awarded"]
    with_amt = [p for p in awarded if p["amount_yuan"]]
    core = [p for p in projects if p["relevance"] in ("high", "medium") and p["nature"] == "service"]
    core_awarded = [p for p in core if p["status"] == "awarded"]

    print(f"公告 {len(clean)} 条 -> 项目 {len(projects)} 个")
    print(f"  已成交项目 {len(awarded)} 个, 其中含金额 {len(with_amt)} 个 "
          f"({len(with_amt)/max(len(awarded),1)*100:.0f}%)")
    print(f"  核心样本(相关性高/中 且 咨询服务) {len(core)} 个, 已成交 {len(core_awarded)} 个")
    if with_amt:
        amts = sorted(p["amount_yuan"] for p in with_amt)
        print(f"  金额中位数 {amts[len(amts)//2]/10000:.1f} 万, 合计 {sum(amts)/10000:.0f} 万")
    tier_cnt = defaultdict(int)
    for p in projects:
        tier_cnt[p["tier"]] += 1
    print(f"  分层: {dict(tier_cnt)}")
    nat = defaultdict(int)
    for p in projects:
        nat[p["nature"]] += 1
    print(f"  性质: {dict(nat)}")
    print(f"-> {outdir / 'projects.json'}")


if __name__ == "__main__":
    main()
