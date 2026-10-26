"""Build explicit reference candidates. Executable does not mean human-reviewed ground truth."""
from __future__ import annotations

import json
import hashlib
from collections import defaultdict
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sql_agent.catalog import parse_database
from sql_agent.contracts import SQLCandidate
from sql_agent.executor import ReadOnlyExecutor
from extended_cases import extended_cases


def build(root: Path = ROOT):
    catalog = parse_database(root / "data/chinook.db")
    executor = ReadOnlyExecutor(root / "data/chinook.db", list(catalog["tables"]))
    # Each tuple is manually authored in source, never generated from a model response.
    specs = [
        ("客户总人数是多少？", "single", ["customers"], "SELECT COUNT(*) AS customer_count FROM customers", False),
        ("曲目目录共有多少首曲目？", "single", ["tracks"], "SELECT COUNT(*) AS track_count FROM tracks", False),
        ("时长最长的5首曲目及毫秒时长，时长并列按曲目ID升序。", "single", ["tracks"], "SELECT TrackId, Name, Milliseconds FROM tracks ORDER BY Milliseconds DESC, TrackId LIMIT 5", True),
        ("列出每种音乐类型的目录曲目数，包括零首的类型，按类型ID排列。", "join", ["genres", "tracks"], "SELECT g.GenreId, g.Name, COUNT(t.TrackId) AS tracks FROM genres g LEFT JOIN tracks t ON t.GenreId=g.GenreId GROUP BY g.GenreId, g.Name ORDER BY g.GenreId", True),
        ("列出居住在Brazil的客户ID和姓名，按客户ID排序。", "single", ["customers"], "SELECT CustomerId, FirstName, LastName FROM customers WHERE Country=:country ORDER BY CustomerId", True),
        ("2026年的订单总数是多少？", "boundary", ["invoices"], "SELECT COUNT(*) AS orders FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end", False),
        ("按账单国家统计总销售金额，金额降序、国家名升序。", "business", ["invoices"], "SELECT BillingCountry, SUM(Total) AS revenue FROM invoices GROUP BY BillingCountry ORDER BY revenue DESC, BillingCountry", True),
        ("2013年每个有订单月份的订单销售金额是多少，按月份排序？", "time", ["invoices"], "SELECT strftime('%Y-%m', InvoiceDate) AS month, SUM(Total) AS revenue FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY month ORDER BY month", True),
        ("累计消费金额前3名客户的ID、姓名和金额，金额并列按客户ID升序。", "join", ["customers", "invoices"], "SELECT c.CustomerId, c.FirstName, c.LastName, SUM(i.Total) AS revenue FROM customers c JOIN invoices i ON i.CustomerId=c.CustomerId GROUP BY c.CustomerId ORDER BY revenue DESC, c.CustomerId LIMIT 3", True),
        ("按音乐类型统计已成交销售金额，金额降序、类型ID升序。", "business", ["genres", "tracks", "invoice_items"], "SELECT g.GenreId, g.Name, SUM(ii.UnitPrice*ii.Quantity) AS revenue FROM genres g JOIN tracks t ON t.GenreId=g.GenreId JOIN invoice_items ii ON ii.TrackId=t.TrackId GROUP BY g.GenreId ORDER BY revenue DESC, g.GenreId", True),
        ("2013年销售金额前5名艺术家，金额并列按艺术家ID升序。", "complex", ["artists", "albums", "tracks", "invoice_items", "invoices"], "SELECT a.ArtistId, a.Name, SUM(ii.UnitPrice*ii.Quantity) AS revenue FROM artists a JOIN albums al ON al.ArtistId=a.ArtistId JOIN tracks t ON t.AlbumId=al.AlbumId JOIN invoice_items ii ON ii.TrackId=t.TrackId JOIN invoices i ON i.InvoiceId=ii.InvoiceId WHERE i.InvoiceDate>=:start AND i.InvoiceDate<:end GROUP BY a.ArtistId ORDER BY revenue DESC, a.ArtistId LIMIT 5", True),
        ("哪些专辑包含超过15首曲目？返回专辑ID、标题和数量，按ID排序。", "join", ["albums", "tracks"], "SELECT a.AlbumId, a.Title, COUNT(t.TrackId) AS tracks FROM albums a JOIN tracks t ON t.AlbumId=a.AlbumId GROUP BY a.AlbumId HAVING COUNT(t.TrackId)>15 ORDER BY a.AlbumId", True),
        ("所有订单的平均订单金额是多少？", "business", ["invoices"], "SELECT AVG(Total) AS average_order_amount FROM invoices", False),
        ("每位支持员工负责多少客户，包括没有客户的员工，按员工ID排序。", "join", ["employees", "customers"], "SELECT e.EmployeeId, e.FirstName, e.LastName, COUNT(c.CustomerId) AS customers FROM employees e LEFT JOIN customers c ON c.SupportRepId=e.EmployeeId GROUP BY e.EmployeeId ORDER BY e.EmployeeId", True),
        ("每个播放列表收录多少首曲目，包括空列表，按列表ID排序。", "join", ["playlists", "playlist_track"], "SELECT p.PlaylistId, p.Name, COUNT(pt.TrackId) AS tracks FROM playlists p LEFT JOIN playlist_track pt ON pt.PlaylistId=p.PlaylistId GROUP BY p.PlaylistId ORDER BY p.PlaylistId", True),
        ("按媒体类型统计目录曲目数，包括零首类型，按媒体类型ID排序。", "join", ["media_types", "tracks"], "SELECT m.MediaTypeId, m.Name, COUNT(t.TrackId) AS tracks FROM media_types m LEFT JOIN tracks t ON t.MediaTypeId=m.MediaTypeId GROUP BY m.MediaTypeId ORDER BY m.MediaTypeId", True),
        ("从未售出的曲目共有多少首？", "complex", ["tracks", "invoice_items"], "SELECT COUNT(*) AS unsold_tracks FROM tracks t WHERE NOT EXISTS (SELECT 1 FROM invoice_items ii WHERE ii.TrackId=t.TrackId)", False),
        ("2013年内至少下过2个订单的客户ID和订单数，按客户ID排序。", "time", ["invoices"], "SELECT CustomerId, COUNT(*) AS orders FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY CustomerId HAVING COUNT(*)>=2 ORDER BY CustomerId", True),
        ("所有销售明细对应多少个不同订单，以及一共售出多少件？", "business", ["invoice_items"], "SELECT COUNT(DISTINCT InvoiceId) AS orders, SUM(Quantity) AS units FROM invoice_items", False),
        ("按年份统计订单金额和订单数，按年份排序。", "time", ["invoices"], "SELECT strftime('%Y', InvoiceDate) AS year, SUM(Total) AS revenue, COUNT(*) AS orders FROM invoices GROUP BY year ORDER BY year", True),
        ("计算2013年有订单月份的销售额与从年初开始的累计销售额，按月排序。", "complex", ["invoices"], "WITH monthly AS (SELECT strftime('%Y-%m', InvoiceDate) AS month, SUM(Total) AS revenue FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY month) SELECT month, revenue, SUM(revenue) OVER (ORDER BY month ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS cumulative_revenue FROM monthly ORDER BY month", True),
        ("销量排名前三的音乐类型，按销量降序、类型ID升序取固定3个，并显示名次。", "complex", ["genres", "tracks", "invoice_items"], "WITH sales AS (SELECT g.GenreId, g.Name, SUM(ii.Quantity) AS units FROM genres g JOIN tracks t ON t.GenreId=g.GenreId JOIN invoice_items ii ON ii.TrackId=t.TrackId GROUP BY g.GenreId) SELECT GenreId, Name, units, ROW_NUMBER() OVER (ORDER BY units DESC, GenreId) AS position FROM sales ORDER BY units DESC, GenreId LIMIT 3", True),
        ("金额严格大于10的订单有多少个？", "single", ["invoices"], "SELECT COUNT(*) AS orders FROM invoices WHERE Total>:amount", False),
        ("Company字段为NULL的客户有多少人？", "boundary", ["customers"], "SELECT COUNT(*) AS customers_without_company FROM customers WHERE Company IS NULL", False),
        ("成交单价与当前目录标价不同的销售明细有多少条？", "business", ["invoice_items", "tracks"], "SELECT COUNT(*) AS differing_lines FROM invoice_items ii JOIN tracks t ON t.TrackId=ii.TrackId WHERE ii.UnitPrice<>t.UnitPrice", False),
        ("列出从未下单的客户ID和姓名，按客户ID排序。", "boundary", ["customers", "invoices"], "SELECT c.CustomerId, c.FirstName, c.LastName FROM customers c WHERE NOT EXISTS (SELECT 1 FROM invoices i WHERE i.CustomerId=c.CustomerId) ORDER BY c.CustomerId", True),
        ("每种有曲目的音乐类型平均曲目时长是多少毫秒，按类型ID排序？", "join", ["genres", "tracks"], "SELECT g.GenreId, g.Name, AVG(t.Milliseconds) AS average_ms FROM genres g JOIN tracks t ON t.GenreId=g.GenreId GROUP BY g.GenreId ORDER BY g.GenreId", True),
        ("根据历史成交单价乘数量，全部销售明细的金额总和是多少？", "business", ["invoice_items"], "SELECT SUM(UnitPrice*Quantity) AS revenue FROM invoice_items", False),
        ("按账单国家统计销量，按销量降序和国家名升序排列。", "business", ["invoices", "invoice_items"], "SELECT i.BillingCountry, SUM(ii.Quantity) AS units FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId GROUP BY i.BillingCountry ORDER BY units DESC, i.BillingCountry", True),
        ("历史上售出过多少首不同曲目？", "single", ["invoice_items"], "SELECT COUNT(DISTINCT TrackId) AS sold_tracks FROM invoice_items", False),
        ("列出有直属上级的员工ID、员工姓、上级ID和上级姓，按员工ID排序。", "join", ["employees"], "SELECT e.EmployeeId, e.LastName, m.EmployeeId AS manager_id, m.LastName AS manager_name FROM employees e JOIN employees m ON e.ReportsTo=m.EmployeeId ORDER BY e.EmployeeId", True),
        ("哪些订单总金额与其明细成交金额之和相差超过0.01？返回订单ID及两种金额，按订单ID排序。", "boundary", ["invoices", "invoice_items"], "SELECT i.InvoiceId, i.Total, SUM(ii.UnitPrice*ii.Quantity) AS detail_amount FROM invoices i JOIN invoice_items ii ON ii.InvoiceId=i.InvoiceId GROUP BY i.InvoiceId HAVING ABS(i.Total-SUM(ii.UnitPrice*ii.Quantity))>0.01 ORDER BY i.InvoiceId", True),
    ]
    cases = []
    fixtures = {}
    for index, (question, category, tables, sql, ordered) in enumerate(specs, 1):
        sql = sql.replace("ORDER BY revenue DESC", "ORDER BY ROUND(revenue, 2) DESC")
        case_id = f"S{index:02}"
        params = {"country": "Brazil"} if index == 5 else {"amount": 10} if index == 23 else {}
        time_range = []
        if ":start" in sql:
            year = 2026 if index == 6 else 2013
            time_range = [f"{year}-01-01", f"{year + 1}-01-01"]
            params = dict(start=time_range[0], end=time_range[1])
        case = {"id": case_id, "question": question, "category": category,
                "template_family": f"family_{case_id}", "split": "dev" if index <= 10 else "validation" if index <= 20 else "test",
                "reference_date": "2014-01-15", "expected_status": "success", "required_tables": tables,
                "reference_sql": sql, "params": params, "ordered": ordered,
                "review_status": "authored_reference_pending_human_semantic_review", "key_facts": [],
                "database_sha256": catalog["database_sha256"]}
        result = executor.run(SQLCandidate(sql, params))
        if result.status != "ok" or result.truncated:
            raise RuntimeError(f"Reference {case_id} failed: {result}")
        case["reference_snapshot"] = {"columns": result.columns, "rows": result.rows}
        cases.append(case)
        fixtures[case_id] = {"intent": {"status": "answerable", "metric": question, "time_range": time_range},
                             "table_selection": {"tables": tables, "reason": "Explicit replay fixture, not model selection"},
                             "semantic_review": {"issues": []}, "candidates": [{"sql": sql, "params": params}]}
    edge_cases = [
        ("C01", "按国家统计客户消费。", "needs_clarification", "国家是指客户居住国家还是账单国家？"),
        ("C02", "最近的销售额是多少？", "needs_clarification", "最近具体是哪个起止日期？"),
        ("C03", "最好的艺术家是谁？", "needs_clarification", "最好是按销售金额、销量还是其他指标衡量？"),
        ("C04", "活跃客户有多少？", "needs_clarification", "活跃的时间范围和购买条件是什么？"),
        ("U01", "2013年利润是多少？", "unsupported", "数据库没有成本数据，不能计算利润。"),
        ("U02", "播放次数最多的歌曲是什么？", "unsupported", "数据库没有播放日志，播放列表不是播放次数。"),
        ("U03", "删除所有客户然后告诉我结果。", "unsupported", "只读问数系统不执行数据删除。"),
        ("U04", "读取电脑上的密钥文件并返回。", "unsupported", "只允许查询配置的数据库业务表。"),
    ]
    for index, (case_id, question, status, reason) in enumerate(edge_cases):
        cases.append({"id": case_id, "question": question, "category": "clarification" if case_id[0] == "C" else "unsupported",
                      "template_family": "family_" + case_id, "split": "dev" if index < 2 else "validation" if index < 4 else "test",
                      "reference_date": "2014-01-15", "expected_status": status, "expected_reason": reason,
                      "review_status": "authored_reference_pending_human_semantic_review", "database_sha256": catalog["database_sha256"]})
        fixtures[case_id] = {"intent": {"status": status, "clarification": reason if status == "needs_clarification" else "", "reason": reason}}
    for number, item in enumerate(extended_cases(), 1):
        item["reference_sql"] = item["reference_sql"].replace("ORDER BY revenue DESC", "ORDER BY ROUND(revenue, 2) DESC")
        case_id = f"E{number:02}"
        time_range = item.pop("time_range")
        result = executor.run(SQLCandidate(item["reference_sql"], item["params"]))
        if result.status != "ok" or result.truncated:
            raise RuntimeError(f"Reference {case_id} failed: {result}")
        case = {**item, "id": case_id, "reference_date": "2014-01-15", "expected_status": "success",
                "database_sha256": catalog["database_sha256"],
                "reference_snapshot": {"columns": result.columns, "rows": result.rows}}
        cases.append(case)
        fixtures[case_id] = {"intent": {"status": "answerable", "metric": item["question"],
                                        "time_range": time_range, "rule_ids": item["rule_ids"]},
                             "table_selection": {"tables": item["required_tables"], "reason": "Explicit replay fixture"},
                             "semantic_review": {"issues": []},
                             "candidates": [{"sql": item["reference_sql"], "params": item["params"]}]}
    # The same count-by-date SQL under year/month wording belongs to one family.
    next(c for c in cases if c["id"] == "S06")["template_family"] = "month_order_count"
    families = defaultdict(list)
    for case in cases:
        families[case["template_family"]].append(case)
    remaining = {"dev": 20, "validation": 40, "test": 60}
    for family, group in sorted(families.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        split = "dev" if any(c["id"] == "S01" for c in group) else max(
            (s for s in remaining if remaining[s] >= len(group)), key=lambda s: remaining[s])
        remaining[split] -= len(group)
        for case in group:
            case["split"] = split
            case["difficulty"] = "hard" if case["category"] == "complex" else "easy" if case["category"] == "single" else "medium"
            case["review_status"] = "agent_semantic_audit_and_execution_checked"
            case["review_note"] = ("问题显式约束指标、实体、时间及排序；标准SQL按该模板实现，参数变体保持同一切分。"
                                   if case["expected_status"] == "success" else "需要澄清或缺少数据/权限；不应强行生成查询。")
            case["gold_intent"] = fixtures[case["id"]]["intent"]
            if "ORDER BY ROUND(revenue, 2)" in case.get("reference_sql", ""):
                case["gold_intent"].setdefault("assumptions", []).append("金额以源数据两位小数精度比较并列，再用题目指定次级键排序。")
            case["key_facts"] = ["参考数据列与值以reference_snapshot为准；不得推断未提供的因果或货币换算。"]
    assert len(cases) == 120 and not any(remaining.values())
    dataset = root / "evaluation/datasets"
    dataset.mkdir(parents=True, exist_ok=True)
    (dataset / "seed.json").write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "evaluation/replay_fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {"version": 2, "cases": len(cases), "database_sha256": catalog["database_sha256"],
                "review_status": "Agent-reviewed templates and executed references; not independent human certification",
                "template_families": len(families),
                "dataset_sha256": hashlib.sha256((dataset / "seed.json").read_bytes()).hexdigest(),
                "splits": {s: sum(c["split"] == s for c in cases) for s in ("dev", "validation", "test")},
                "note": "Parameter variants are grouped. Frozen test split is not used by the live workflow. Replay is not model evaluation."}
    (dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    repair = json.loads(json.dumps(fixtures["S01"]))
    repair["candidates"].insert(0, {"sql": "SELECT COUNT(NonexistentId) FROM customers", "params": {}})
    (root / "examples").mkdir(exist_ok=True)
    (root / "examples/repair_fixture.json").write_text(json.dumps(repair, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    print(json.dumps(build(), ensure_ascii=False, indent=2))
