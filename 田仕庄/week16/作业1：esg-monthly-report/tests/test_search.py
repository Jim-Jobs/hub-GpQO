"""检索层契约：去重（URL + 标题相似度），同一事件取最早来源（README §8.1）。

实现 scripts/search.py 时对齐：
    dedupe(results) -> list[dict]
"""

import pytest

search = pytest.importorskip("scripts.search", reason="scripts 未落地，契约先行")


def item(url, title, published="2026-10-10"):
    return {"url": url, "title": title, "published_at": published}


class TestDedupe:
    def test_same_url_kept_once(self):
        items = [item("https://a.com/1", "标题甲"), item("https://a.com/1", "完全不同的标题乙")]
        assert len(search.dedupe(items)) == 1

    def test_similar_titles_kept_once(self):
        items = [
            item("https://a.com/1", "国家能源局发布新型储能发展指导意见"),
            item("https://b.com/2", "国家能源局发布《新型储能发展指导意见》"),
        ]
        assert len(search.dedupe(items)) == 1

    def test_distinct_titles_both_kept(self):
        items = [
            item("https://a.com/1", "国家能源局发布新型储能发展指导意见"),
            item("https://c.com/3", "中国神华发布半年度社会责任报告"),
        ]
        assert len(search.dedupe(items)) == 2

    def test_earliest_source_wins(self):
        items = [
            item("https://a.com/1", "同一事件的报道", published="2026-10-05"),
            item("https://b.com/2", "同一事件的报道", published="2026-10-03"),
        ]
        result = search.dedupe(items)
        assert len(result) == 1
        assert result[0]["published_at"] == "2026-10-03"


class TestCrossSiteDedupe:
    def test_site_suffix_variants_merge(self):
        title = "工业和信息化部关于印发《碳排放双控工作体系实施方案》的通知"
        items = [
            item("https://a.gov.cn/1", title + " _ 国家政策法规 _ 省工信厅"),
            item("https://b.gov.cn/2", title),
        ]
        assert len(search.dedupe(items)) == 1

    def test_notice_wrapper_and_book_marks_merge(self):
        items = [
            item(
                "https://a.gov.cn/1",
                "工业和信息化部关于印发《加快构建碳排放双控工作体系实施方案(2026-2030年)》的通知",
            ),
            item(
                "https://c.gov.cn/3",
                "加快构建碳排放双控工作体系实施方案（2026—2030年）",
            ),
        ]
        assert len(search.dedupe(items)) == 1

    def test_stock_code_and_site_suffix_merge(self):
        items = [
            item(
                "https://a.com/1",
                "中国神华(601088):中国神华关于举办2026年第三季度业绩说明会- CFi.CN 中财网",
            ),
            item(
                "https://b.com/2",
                "中国神华（601088）关于举办2026年第三季度业绩说明会",
            ),
        ]
        assert len(search.dedupe(items)) == 1

    def test_multi_segment_site_chain_merges(self):
        core = "工业和信息化部关于印发《加快构建碳排放双控工作体系实施方案》的通知"
        items = [
            item(
                "https://a.gov.cn/1",
                core + " - 国家政策 - 广西南宁市人民政府门户网站 - www.nanning.gov.cn",
            ),
            item("https://b.gov.cn/2", core + "_国家级文件_天津市工业和信息化局"),
        ]
        assert len(search.dedupe(items)) == 1

    def test_related_but_distinct_policies_kept(self):
        items = [
            item("https://a.gov.cn/1", "关于印发《碳排放双控工作体系实施方案》的通知"),
            item("https://b.gov.cn/2", "关于印发《碳排放核算指南》的通知"),
        ]
        assert len(search.dedupe(items)) == 2


class TestSource:
    def test_site_name_preferred(self):
        raw = {
            "name": "中国神华获奖",
            "url": "https://www.cpnn.com.cn/a.html",
            "snippet": "摘要",
            "datePublished": "2026-10-20",
            "siteName": "中国能源新闻网",
        }
        item = search._normalize(
            raw,
            "bocha",
            title_key="name",
            url_key="url",
            snippet_key="snippet",
            date_key="datePublished",
            source_key="siteName",
        )
        assert item["source"] == "中国能源新闻网"

    def test_falls_back_to_url_host(self):
        raw = {"title": "标题", "url": "https://www.example.com/a"}
        item = search._normalize(
            raw, "tavily", title_key="title", url_key="url", snippet_key="snippet", date_key="date"
        )
        assert item["source"] == "example.com"


class TestSearchError:
    def test_error_carries_http_status_and_message(self, monkeypatch):
        class Response:
            status_code = 403
            reason = "Forbidden"
            text = '{"message":"You do not have enough money or package quota"}'

            def raise_for_status(self):
                raise search.requests.HTTPError("403 Client Error", response=self)

        monkeypatch.setattr(search.requests, "post", lambda *args, **kwargs: Response())
        monkeypatch.setattr(search.time, "sleep", lambda seconds: None)
        client = search.SearchClient("bocha", "test-key", min_interval=0)

        with pytest.raises(search.SearchError) as excinfo:
            client.search("测试查询")

        message = str(excinfo.value)
        assert "HTTP 403" in message
        assert "enough money or package quota" in message
        assert "测试查询" in message
