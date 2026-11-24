#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
CCGP 招标公告抓取器 (中国政府采购网)

设计要点:
  1. ccgp 部署了 WAF, 裸 HTTP 请求一律 403/频次拦截, 必须用真实浏览器内核。
     因此统一走 Playwright Chromium, 并在首次请求前先访问首页养 cookie。
  2. 站内检索为"标题检索", 单一关键词召回有限, 需按关键词矩阵逐个检索后去重。
  3. 严格串行 + 随机间隔。并发会立刻触发"访问过于频繁", 反而更慢。
  4. 全量落盘 + URL 哈希缓存, 中断后重跑只补缺失, 天然支持断点续抓。

用法:
  python ccgp_crawler.py --mode history --start 2024-11-01 --end 2026-11-24
  python ccgp_crawler.py --mode daily --days 1
  python ccgp_crawler.py --mode history --no-detail --max-pages 2   # 只抓列表, 快速探路
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote

from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SEARCH_URL = "https://search.ccgp.gov.cn/bxsearch"
HOME_URL = "https://www.ccgp.gov.cn/"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
BLOCK_MARKERS = ("访问过于频繁", "频繁访问", "您的访问过于频繁")

DEFAULT_KEYWORDS = [
    "ESG", "社会责任报告", "可持续发展报告", "可持续披露", "双碳",
    "碳达峰", "碳中和", "碳核查", "碳盘查", "碳排放",
    "碳资产", "碳交易", "碳足迹", "零碳", "低碳园区",
    "绿色低碳", "绿色工厂", "节能诊断", "温室气体", "气候投融资",
]


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def uid(url: str) -> str:
    return hashlib.md5(url.encode("utf-8")).hexdigest()[:16]


def clean(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"[\u3000\xa0]+", " ", text)
    t = re.sub(r"[ \t\r\n]+", " ", t)
    return t.strip()


# ---------------------------------------------------------------- 页面解析

def build_search_url(kw: str, start: date, end: date, page: int) -> str:
    return (
        f"{SEARCH_URL}?searchtype=1&page_index={page}&bidSort=0"
        f"&buyerName=&projectId=&pinMu=0&bidType=0&dbselect=bidx"
        f"&kw={quote(kw)}"
        f"&start_time={start:%Y}%3A{start:%m}%3A{start:%d}"
        f"&end_time={end:%Y}%3A{end:%m}%3A{end:%d}"
        f"&timeType=6&displayZone=&zoneId=&pppStatus=0&agentName="
    )


def parse_total(html: str) -> int | None:
    # 总数常被 <span> 包裹, 如 "共找到<span>110</span>条内容", 正则需跨标签
    m = re.search(r"共找到[\s\S]{0,200}?([\d,]{1,9})\s*条", html)
    if m:
        return int(m.group(1).replace(",", ""))
    m = re.search(r"共找到\s*([\d,]+)\s*条", BeautifulSoup(html, "lxml").get_text(" ", strip=True))
    return int(m.group(1).replace(",", "")) if m else None


def parse_list(html: str, keyword: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    out: list[dict] = []
    for li in soup.select("ul.vT-srch-result-list-bid > li"):
        a = li.find("a", href=True)
        if not a:
            continue
        url = a["href"].strip()
        if not url.startswith("http"):
            url = "http:" + url if url.startswith("//") else url
        title = clean(a.get_text("", strip=True))
        if not title:
            continue

        p = li.find("p")
        summary = clean(p.get_text(" ", strip=True)) if p else ""

        span = li.find("span")
        tokens = []
        if span:
            tokens = [clean(t) for t in span.get_text("|").split("|")]
            tokens = [t for t in tokens if t]

        pub_date, buyer, agency, ntype, region = "", "", "", "", ""
        extras = []
        for t in tokens:
            if not pub_date and re.match(r"^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}", t):
                pub_date = re.match(r"^(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", t).group(0)
                pub_date = pub_date.replace(".", "-").replace("/", "-")
                continue
            if t.startswith("采购人"):
                buyer = re.sub(r"^采购人[：:]\s*", "", t)
                continue
            if t.startswith("代理机构"):
                agency = re.sub(r"^代理机构[：:]\s*", "", t)
                continue
            extras.append(t)

        # strong 标签更精确地标识公告类型
        strongs = [clean(s.get_text()) for s in (span.find_all("strong") if span else [])]
        strongs = [s for s in strongs if s]
        if strongs:
            ntype = strongs[0]
            extras = [e for e in extras if e != ntype]

        for e in extras:
            if not ntype and re.search(r"(公告|公示|结果|通知|更正|答疑|废标|终止|流标)", e):
                ntype = e
            elif not region and re.fullmatch(r"[\u4e00-\u9fa5]{2,4}", e):
                region = e

        if not ntype:
            for e in extras:
                if re.search(r"(公告|公示|结果|通知)$", e):
                    ntype = e
                    break

        out.append({
            "id": uid(url),
            "title": title,
            "summary": summary,
            "pub_date": pub_date,
            "buyer": buyer,
            "agency": agency,
            "notice_type": ntype,
            "region": region,
            "detail_url": url,
            "keyword": keyword,
            "source": "ccgp",
        })
    return out


def parse_detail_text(html: str) -> str:
    """提取详情正文。

    关键: 政府采购公告的关键信息(供应商/金额)常放在 <table> 里, 若直接把整页拍平成
    纯文本, 列顺序和对齐关系会丢失, 导致金额与字段错配。因此先把每个 <tr> 压成一行,
    用 " || " 分隔单元格, 保持"表头行 -> 数据行"的相邻关系, 供下游按列索引取值。
    """
    soup = BeautifulSoup(html, "lxml")
    node = (
        soup.select_one("div.vF_detail_content")
        or soup.select_one("div.vF_detail")
        or soup.select_one("#content")
        or soup.body
    )
    if not node:
        return ""
    for bad in node.select("script, style, .vF_detail_toolbar, .detail_toolbar, a"):
        bad.decompose()

    # 内层表格优先, 避免嵌套 table 被重复展平
    for table in reversed(node.find_all("table")):
        rows = []
        for tr in table.find_all("tr"):
            cells = [clean(c.get_text(" ", strip=True)) for c in tr.find_all(["td", "th"])]
            # 必须保留空单元格占位, 否则表头列数与数据行列数不一致, 列索引会错位
            if any(cells):
                rows.append(" || ".join(cells))
        if rows:
            table.replace_with(soup.new_string("\n" + "\n".join(rows) + "\n"))

    text = node.get_text("\n", strip=True)
    text = re.sub(r"\n{2,}", "\n", text)
    return text[:12000]


# ---------------------------------------------------------------- 抓取器

class Crawler:
    def __init__(self, outdir: Path, min_delay=2.0, max_delay=3.2, headless=True):
        self.outdir = outdir
        self.cache = outdir / "cache"
        self.raw = outdir / "raw"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.raw.mkdir(parents=True, exist_ok=True)
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.headless = headless
        self.page = None
        self.pw = None
        self.browser = None
        self.blocked = 0
        self.req_count = 0

    async def __aenter__(self):
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=self.headless)
        ctx = await self.browser.new_context(
            user_agent=UA, viewport={"width": 1440, "height": 900}, locale="zh-CN"
        )
        ctx.set_default_timeout(60000)
        self.page = await ctx.new_page()
        # 养 cookie, 绕过首次请求的 WAF 判定
        try:
            await self.page.goto(HOME_URL, timeout=60000, wait_until="domcontentloaded")
            await asyncio.sleep(1.5)
        except Exception as e:
            log(f"首页预热失败(忽略): {type(e).__name__}")
        return self

    async def __aexit__(self, *exc):
        try:
            if self.browser:
                await self.browser.close()
            if self.pw:
                await self.pw.stop()
        except Exception:
            pass

    async def _sleep(self):
        await asyncio.sleep(random.uniform(self.min_delay, self.max_delay))

    async def get(self, url: str, tries: int = 4) -> str | None:
        for attempt in range(tries):
            try:
                await self.page.goto(url, timeout=60000, wait_until="domcontentloaded")
                html = await self.page.content()
                self.req_count += 1
                if any(m in html for m in BLOCK_MARKERS):
                    self.blocked += 1
                    wait = 10 + 8 * attempt
                    log(f"  触发限流, 退避 {wait}s (第{attempt+1}次)")
                    await asyncio.sleep(wait)
                    continue
                return html
            except Exception as e:
                log(f"  请求异常 {type(e).__name__}, 重试 {attempt+1}/{tries}")
                await asyncio.sleep(4 + 4 * attempt)
        return None

    async def search_page(self, kw: str, start: date, end: date, page: int):
        html = await self.get(build_search_url(kw, start, end, page))
        await self._sleep()
        if html is None:
            return None, 0, []
        return html, parse_total(html) or 0, parse_list(html, kw)

    async def crawl_range(self, kw: str, start: date, end: date,
                          max_pages: int, max_per_query: int = 400) -> list[dict]:
        """抓一个时间区间; 结果过多时自动二分切片, 规避分页上限。"""
        html, total, records = await self.search_page(kw, start, end, 1)
        if html is None:
            return []

        if total > max_per_query and (end - start).days > 20:
            mid = start + timedelta(days=(end - start).days // 2)
            log(f"  {kw} [{start}~{end}] 命中 {total} 条, 拆分区间")
            left = await self.crawl_range(kw, start, mid, max_pages, max_per_query)
            right = await self.crawl_range(kw, mid + timedelta(days=1), end, max_pages, max_per_query)
            return left + right

        pages = min(max_pages, (total + 19) // 20) if total else 1
        all_recs = list(records)
        for p in range(2, pages + 1):
            _, _, recs = await self.search_page(kw, start, end, p)
            if not recs:
                break
            all_recs.extend(recs)
        return all_recs


def split_by_year(start: date, end: date) -> list[tuple[date, date]]:
    """把区间按自然年切分。

    站内检索对时间跨度有硬限制: 查询跨度超过约 1 年时, 接口不会报错,
    而是**静默返回 0 条**。这是最隐蔽的坑 —— 日志看起来一切正常, 数据却是空的。
    因此无论用户传什么区间, 都先切成自然年再逐段查询。
    """
    segs: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        seg_end = min(date(cur.year, 12, 31), end)
        segs.append((cur, seg_end))
        cur = seg_end + timedelta(days=1)
    return segs


# ---------------------------------------------------------------- 缓存

def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows


def append_jsonl(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 主流程

async def run(args) -> int:
    outdir = Path(args.out).resolve()
    kw_file = Path(args.keywords_file) if args.keywords_file else Path(__file__).parent.parent / "references" / "taxonomy.json"

    if args.keywords:
        keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
    elif kw_file.exists():
        keywords = json.loads(kw_file.read_text(encoding="utf-8")).get("search_keywords", DEFAULT_KEYWORDS)
    else:
        keywords = DEFAULT_KEYWORDS

    if args.mode == "daily":
        end = date.today()
        start = end - timedelta(days=max(args.days, 1) - 1)
    else:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()

    log(f"模式={args.mode} 区间={start}~{end} 关键词={len(keywords)} 输出={outdir}")

    list_cache = outdir / "cache" / "list.jsonl"
    detail_dir = outdir / "cache" / "detail"
    detail_dir.mkdir(parents=True, exist_ok=True)

    async with Crawler(outdir, args.min_delay, args.max_delay, not args.headful) as cr:
        (outdir / "raw").mkdir(parents=True, exist_ok=True)
        lu = outdir / "raw" / "list_unique.json"

        if args.from_cache and lu.exists():
            # 列表已抓完时直接复用, 避免为了补详情重复跑几十分钟的检索请求
            notices = json.loads(lu.read_text(encoding="utf-8"))
            log(f"[from-cache] 载入 {len(notices)} 条公告, 跳过列表检索")
        else:
            # ---- 阶段 1: 检索列表
            all_recs: list[dict] = []
            segs = split_by_year(start, end)
            if len(segs) > 1:
                log(f"区间跨 {len(segs)} 个自然年, 按年分段查询: "
                    + ", ".join(f"{a}~{b}" for a, b in segs))
            for i, kw in enumerate(keywords, 1):
                recs: list[dict] = []
                for seg_start, seg_end in segs:
                    recs += await cr.crawl_range(kw, seg_start, seg_end, args.max_pages)
                log(f"[{i}/{len(keywords)}] {kw}: {len(recs)} 条")
                if recs:
                    append_jsonl(list_cache, recs)
                    all_recs.extend(recs)

            # 跨关键词去重 (以 list.jsonl 全量为准, 便于断点续跑)
            merged: dict[str, dict] = {}
            for r in load_jsonl(list_cache):
                rid = r["id"]
                if rid in merged:
                    kws = merged[rid].setdefault("keywords", [merged[rid].get("keyword", "")])
                    if r.get("keyword") and r["keyword"] not in kws:
                        kws.append(r["keyword"])
                else:
                    r["keywords"] = [r.get("keyword", "")] if r.get("keyword") else []
                    merged[rid] = r

            notices = list(merged.values())
            log(f"去重后唯一公告: {len(notices)} 条 (原始 {len(all_recs)} 条, 请求 {cr.req_count} 次, 限流 {cr.blocked} 次)")
            with lu.open("w", encoding="utf-8") as f:
                json.dump(notices, f, ensure_ascii=False, indent=1)

        if args.no_detail:
            log("已跳过详情抓取 (--no-detail)")
            return len(notices)

        # ---- 阶段 2: 抓详情正文 (只抓未缓存的)
        targets = notices
        if args.detail_types:
            want = [t.strip() for t in args.detail_types.split(",")]
            targets = [n for n in notices if any(w in (n.get("notice_type") or "") for w in want)]
            log(f"按公告类型过滤后需详情: {len(targets)} 条")

        todo = [n for n in targets if not (detail_dir / f"{n['id']}.txt").exists()]
        if args.limit_details:
            todo = todo[: args.limit_details]
        log(f"待抓详情 {len(todo)} / {len(targets)} (其余命中缓存)")

        t0 = time.time()
        for idx, n in enumerate(todo, 1):
            html = await cr.get(n["detail_url"])
            await cr._sleep()
            text = parse_detail_text(html) if html else ""
            (detail_dir / f"{n['id']}.txt").write_text(text, encoding="utf-8")
            if idx % 20 == 0 or idx == len(todo):
                rate = idx / max(time.time() - t0, 1) * 60
                eta = (len(todo) - idx) / max(rate, 0.01)
                log(f"  详情 {idx}/{len(todo)} | {rate:.1f} 条/分 | 预计剩余 {eta:.0f} 分")

        # ---- 阶段 3: 汇总
        final = []
        for n in notices:
            p = detail_dir / f"{n['id']}.txt"
            n["detail_text"] = p.read_text(encoding="utf-8") if p.exists() else ""
            final.append(n)

        dest = outdir / "raw" / f"notices_{start}_{end}.json"
        with dest.open("w", encoding="utf-8") as f:
            json.dump(final, f, ensure_ascii=False, indent=1)

        got = sum(1 for n in final if n.get("detail_text"))
        log(f"完成: {len(final)} 条公告, {got} 条含详情正文 -> {dest}")
        return len(final)


def main():
    ap = argparse.ArgumentParser(description="CCGP 招标公告抓取器")
    ap.add_argument("--mode", choices=["history", "daily"], default="history")
    ap.add_argument("--start", default=None, help="起始日 YYYY-MM-DD (history 模式)")
    ap.add_argument("--end", default=None, help="结束日 YYYY-MM-DD (history 模式)")
    ap.add_argument("--days", type=int, default=1, help="daily 模式回溯天数")
    ap.add_argument("--out", default="./esg_bid_data")
    ap.add_argument("--keywords", default=None, help="逗号分隔, 覆盖默认矩阵")
    ap.add_argument("--keywords-file", default=None)
    ap.add_argument("--max-pages", type=int, default=30)
    ap.add_argument("--min-delay", type=float, default=2.0)
    ap.add_argument("--max-delay", type=float, default=3.2)
    ap.add_argument("--no-detail", action="store_true")
    ap.add_argument("--from-cache", action="store_true",
                    help="跳过列表检索, 直接读 raw/list_unique.json 进入详情阶段(补抓详情时用)")
    ap.add_argument("--detail-types", default="中标,成交,结果",
                    help="只对这些公告类型抓详情, 逗号分隔; 传 all 抓全部")
    ap.add_argument("--limit-details", type=int, default=0)
    ap.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    args = ap.parse_args()

    if args.mode == "history":
        today = date.today()
        args.end = args.end or today.isoformat()
        args.start = args.start or (today - timedelta(days=730)).isoformat()
        if args.detail_types == "all":
            args.detail_types = ""
    n = asyncio.run(run(args))
    log(f"OK: {n}")


if __name__ == "__main__":
    main()
