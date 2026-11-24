"""检索层：多源查询、限流重试、去重、时间窗口归属（README §8.1、§12）。"""

from __future__ import annotations

import calendar
import difflib
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import requests

TITLE_SIMILARITY_THRESHOLD = 0.9
SUPPLEMENT_DAYS = 30
PROVIDERS = ("tavily", "serpapi", "bocha", "bing")


def parse_date(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("/", "-")
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(normalized[:10], fmt).date()
        except ValueError:
            continue
    return None


def normalize_url(url: str) -> str:
    parts = urllib.parse.urlsplit((url or "").strip())
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), parts.query, "")
    )


def _earlier(first: dict, second: dict) -> dict:
    first_date = parse_date(first.get("published_at"))
    second_date = parse_date(second.get("published_at"))
    if first_date is None:
        return second if second_date is not None else first
    if second_date is None:
        return first
    return first if first_date <= second_date else second


_SITE_SPLIT = re.compile(r"\s*(?:--|\||｜|_)\s*|\s+-\s*|\s*-\s+")
_STOCK_CODE = re.compile(r"[（(]\s*\d{5,6}\s*[)）]")
_TITLE_NOISE = ("关于印发", "关于", "的通知", "通知", "的公告", "公告")
_TITLE_PUNCT = re.compile(r"[\s，。；：、,.;:!?！？（）()《》「」“”\"'·\-—_【】{}]+")


def _strip_site_decorations(text: str) -> str:
    """去掉站点 / 栏目片段：先剪首尾短片段，再在片段中取占六成以上的最长正文。"""
    parts = [part.strip() for part in _SITE_SPLIT.split(text.strip()) if part.strip()]
    while len(parts) > 1 and len(parts[0]) <= 6:
        parts.pop(0)
    while len(parts) > 1 and len(parts[-1]) <= 6:
        parts.pop()
    if not parts:
        return text
    joined = "".join(parts)
    core = max(parts, key=len)
    return core if len(core) >= 0.6 * len(joined) else joined


def _canonical_title(title: str) -> str:
    """去掉站点前后缀、公文套话与标点，用于跨站点同名事件比对。"""
    text = _strip_site_decorations(title or "")
    text = _STOCK_CODE.sub("", text)
    for word in _TITLE_NOISE:
        text = text.replace(word, "")
    return _TITLE_PUNCT.sub("", text).lower()


def _same_event(first_title: str, second_title: str) -> bool:
    first = _canonical_title(first_title)
    second = _canonical_title(second_title)
    if not first or not second:
        return first == second
    if first == second:
        return True
    shorter, longer = sorted((first, second), key=len)
    if len(shorter) >= 12 and shorter in longer:
        return True
    return difflib.SequenceMatcher(None, first, second).ratio() >= TITLE_SIMILARITY_THRESHOLD


def dedupe(results: list[dict]) -> list[dict]:
    """同一 URL 或归一化标题判为同一事件的视为重复，保留最早来源。"""
    kept: list[dict] = []
    for item in results:
        item_url = normalize_url(item.get("url", ""))
        matched = None
        for index, existing in enumerate(kept):
            same_url = bool(item_url) and item_url == normalize_url(existing.get("url", ""))
            similar_title = _same_event(existing.get("title", ""), item.get("title", ""))
            if same_url or similar_title:
                matched = index
                break
        if matched is None:
            kept.append(dict(item))
        else:
            kept[matched] = _earlier(kept[matched], item)
    return kept


def window_bounds(month: str) -> tuple[date, date]:
    """返回（主窗口起始日前 30 天，主窗口月末）的闭区间。"""
    year, mon = (int(part) for part in month.split("-"))
    first_day = date(year, mon, 1)
    last_day = date(year, mon, calendar.monthrange(year, mon)[1])
    return first_day - timedelta(days=SUPPLEMENT_DAYS), last_day


def event_in_window(event_date, month: str) -> bool:
    start, end = window_bounds(month)
    parsed = parse_date(event_date)
    return parsed is not None and start <= parsed <= end


class SearchError(RuntimeError):
    pass


def _site_label(raw: dict, url: str, source_key: str | None) -> str:
    """优先取结果自带的站点名，缺失时回退到 URL 域名（去掉 www.）。"""
    if source_key:
        label = str(raw.get(source_key) or "").strip()
        if label:
            return label
    host = urllib.parse.urlsplit(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _normalize(
    raw: dict,
    provider: str,
    *,
    title_key: str,
    url_key: str,
    snippet_key: str,
    date_key: str,
    source_key: str | None = None,
    snippet_fallback: str | None = None,
) -> dict:
    url = (raw.get(url_key) or "").strip()
    snippet = (raw.get(snippet_key) or "").strip()
    if not snippet and snippet_fallback:
        snippet = (raw.get(snippet_fallback) or "").strip()
    return {
        "title": (raw.get(title_key) or "").strip(),
        "url": url,
        "snippet": snippet,
        "published_at": raw.get(date_key) or "",
        "source": _site_label(raw, url, source_key) or provider,
    }


def _describe_error(exc: Exception | None) -> str:
    """把异常转成可读原因：HTTP 错误带状态码与响应消息，便于定位配额 / 鉴权问题。"""
    if exc is None:
        return "未知错误"
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", "?")
        try:
            body = " ".join((response.text or "").split())[:160]
        except Exception:
            body = ""
        detail = body or getattr(response, "reason", "") or ""
        return f"HTTP {status}：{detail}".rstrip("：")
    message = " ".join(str(exc).split())
    return (message or type(exc).__name__)[:160]


class SearchClient:
    """搜索 API 适配层：限流、超时、指数退避重试。"""

    def __init__(
        self,
        provider: str,
        api_key: str,
        *,
        timeout: int = 15,
        max_retries: int = 3,
        min_interval: float = 0.5,
    ):
        provider = (provider or "").lower()
        if provider not in PROVIDERS:
            raise ValueError(f"不支持的搜索提供方：{provider}（可选 {', '.join(PROVIDERS)}）")
        self.provider = provider
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_call = 0.0

    def _throttle(self) -> None:
        with self._lock:
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    def search(self, query: str) -> list[dict]:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                self._throttle()
                return self._request(query)
            except Exception as exc:
                last_error = exc
                time.sleep(min(0.5 * (2**attempt), 4.0))
        raise SearchError(
            f"检索失败（已重试 {self.max_retries} 次）：{query}；原因：{_describe_error(last_error)}"
        ) from last_error

    def _request(self, query: str) -> list[dict]:
        if self.provider == "tavily":
            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.api_key,
                    "query": query,
                    "max_results": 10,
                    "search_depth": "basic",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            return [
                _normalize(
                    item, self.provider, title_key="title", url_key="url",
                    snippet_key="content", date_key="published_date",
                )
                for item in response.json().get("results", [])
            ]

        if self.provider == "serpapi":
            params: dict[str, str | int] = {
                "q": query, "engine": "google", "num": 10, "api_key": self.api_key
            }
            response = requests.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            return [
                _normalize(
                    item, self.provider, title_key="title", url_key="link",
                    snippet_key="snippet", date_key="date",
                )
                for item in response.json().get("organic_results", [])
            ]

        if self.provider == "bing":
            bing_params: dict[str, str | int] = {"q": query, "count": 10}
            response = requests.get(
                "https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": self.api_key},
                params=bing_params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            pages = response.json().get("webPages", {}).get("value", [])
            return [
                _normalize(
                    item, self.provider, title_key="name", url_key="url",
                    snippet_key="snippet", date_key="datePublished",
                )
                for item in pages
            ]

        response = requests.post(
            "https://api.bochaai.com/v1/web-search",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": query, "summary": True, "count": 10},
            timeout=self.timeout,
        )
        response.raise_for_status()
        pages = response.json().get("data", {}).get("webPages", {}).get("value", [])
        return [
            _normalize(
                item, self.provider, title_key="name", url_key="url",
                snippet_key="summary", date_key="datePublished",
                source_key="siteName", snippet_fallback="snippet",
            )
            for item in pages
        ]


def fetch_all(client: SearchClient, queries: list[dict], *, max_workers: int = 4) -> tuple[list[dict], list[dict]]:
    """并发检索；单 query 失败只记缺口，不阻塞整体（README §17）。"""
    results: list[dict] = []
    gaps: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(client.search, entry["query"]): entry for entry in queries}
        for future in as_completed(futures):
            entry = futures[future]
            try:
                fetched = future.result()
            except Exception as exc:
                gaps.append({"query": entry["query"], "category": entry["category"], "reason": str(exc)})
                continue
            stamp = datetime.now().isoformat(timespec="seconds")
            for item in fetched:
                enriched = dict(item)
                enriched.update(
                    {
                        "query": entry["query"],
                        "category": entry["category"],
                        "api": client.provider,
                        "fetched_at": stamp,
                    }
                )
                results.append(enriched)
    return results, gaps


if __name__ == "__main__":
    samples = [
        {
            "title": "国家能源局发布煤炭产能储备管理办法",
            "url": "https://www.nea.gov.cn/art/2026/10/8/a.html?from=list",
            "published_at": "2026-10-08",
        },
        {
            "title": "国家能源局发布煤炭产能储备管理办法",
            "url": "https://finance.example.com/news/1024",
            "published_at": "2026-10-05",
        },
        {
            "title": "中国神华发布三季度运营数据",
            "url": "https://www.shenhua.com/news/1",
            "published_at": "2026-10-12",
        },
    ]
    print(f"去重前 {len(samples)} 条：")
    for item in dedupe(samples):
        print(f" - {item['published_at']} {item['title']} {item['url']}")
    print("2026-10 窗口范围：", window_bounds("2026-10"))
    print("2026-09-15 属于窗口：", event_in_window("2026-09-15", "2026-10"))
