"""Explicit parameter families: variants of one template must remain in one split."""
from __future__ import annotations


def extended_cases():
    result = []

    def add(family, category, tables, sql, variants, ordered=False, rules=None):
        for question, params in variants:
            result.append({"question": question, "category": category, "required_tables": tables,
                           "reference_sql": sql, "params": params, "ordered": ordered,
                           "template_family": family, "rule_ids": rules or [],
                           "time_range": [params["start"], params["end"]] if "start" in params else []})

    add("customer_count_country", "single", ["customers"],
        "SELECT COUNT(*) AS customers FROM customers WHERE Country=:country",
        [(f"居住在{country}的客户有多少人？", {"country": country}) for country in ("USA", "Canada")])
    add("order_count_billing_country", "single", ["invoices"],
        "SELECT COUNT(*) AS orders FROM invoices WHERE BillingCountry=:country",
        [(f"账单国家为{country}的订单有多少个？", {"country": country}) for country in ("USA", "Germany")])
    add("revenue_billing_country", "business", ["invoices"],
        "SELECT SUM(Total) AS revenue FROM invoices WHERE BillingCountry=:country",
        [(f"账单国家为{country}的订单金额总和是多少？", {"country": country}) for country in ("Brazil", "France")], rules=["R01", "R03"])
    years = (2010, 2012)
    year_params = lambda year: {"start": f"{year}-01-01", "end": f"{year+1}-01-01"}
    add("year_revenue", "time", ["invoices"],
        "SELECT SUM(Total) AS revenue FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end",
        [(f"{y}年订单总金额是多少？", year_params(y)) for y in years], rules=["R01", "R05"])
    add("year_unit_sales", "time", ["invoices", "invoice_items"],
        "SELECT SUM(ii.Quantity) AS units FROM invoice_items ii JOIN invoices i ON i.InvoiceId=ii.InvoiceId WHERE i.InvoiceDate>=:start AND i.InvoiceDate<:end",
        [(f"{y}年按订单明细数量统计售出多少件？", year_params(y)) for y in years], rules=["R02", "R05"])
    add("monthly_order_count", "time", ["invoices"],
        "SELECT strftime('%Y-%m', InvoiceDate) AS month, COUNT(*) AS orders FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY month ORDER BY month",
        [(f"{y}年有订单的各月份分别有多少个订单，按月份升序？", year_params(y)) for y in years], True)
    add("monthly_average_order", "time", ["invoices"],
        "SELECT strftime('%Y-%m', InvoiceDate) AS month, AVG(Total) AS average_amount FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY month ORDER BY month",
        [(f"{y}年各有订单月份的平均订单金额，按月份升序。", year_params(y)) for y in years], True, ["R07"])
    add("artist_catalog_track_count", "join", ["artists", "albums", "tracks"],
        "SELECT COUNT(t.TrackId) AS tracks FROM artists ar JOIN albums al ON al.ArtistId=ar.ArtistId JOIN tracks t ON t.AlbumId=al.AlbumId WHERE ar.ArtistId=:artist",
        [(f"艺术家ID为{a}的目录曲目共有多少首？", {"artist": a}) for a in (1, 2)])
    add("artist_album_list", "join", ["albums"],
        "SELECT AlbumId, Title FROM albums WHERE ArtistId=:artist ORDER BY AlbumId",
        [(f"列出艺术家ID为{a}的专辑ID和标题，按专辑ID升序。", {"artist": a}) for a in (1, 2)], True)
    add("genre_track_count", "single", ["tracks"],
        "SELECT COUNT(*) AS tracks FROM tracks WHERE GenreId=:genre",
        [(f"类型ID为{g}的目录曲目共有多少首？", {"genre": g}) for g in (1, 2)])
    add("genre_catalog_average_price", "business", ["tracks"],
        "SELECT AVG(UnitPrice) AS average_catalog_price FROM tracks WHERE GenreId=:genre",
        [(f"类型ID为{g}的曲目当前目录标价平均值是多少？", {"genre": g}) for g in (1, 2)], rules=["R04"])
    add("album_duration", "join", ["tracks"],
        "SELECT SUM(Milliseconds) AS duration_ms FROM tracks WHERE AlbumId=:album",
        [(f"专辑ID为{a}的所有曲目总时长是多少毫秒？", {"album": a}) for a in (1, 2)])
    add("invoice_detail_list", "join", ["invoice_items", "tracks"],
        "SELECT ii.InvoiceLineId, t.Name, ii.UnitPrice, ii.Quantity, ii.UnitPrice*ii.Quantity AS line_amount FROM invoice_items ii JOIN tracks t ON t.TrackId=ii.TrackId WHERE ii.InvoiceId=:invoice ORDER BY ii.InvoiceLineId",
        [(f"列出订单ID为{i}的明细ID、曲目名、成交单价、数量和明细金额，按明细ID排序。", {"invoice": i}) for i in (1, 2)], True, ["R04"])
    add("customer_order_history", "single", ["invoices"],
        "SELECT InvoiceId, InvoiceDate, Total FROM invoices WHERE CustomerId=:customer ORDER BY InvoiceDate, InvoiceId",
        [(f"列出客户ID为{c}的订单ID、日期和金额，按日期和订单ID排序。", {"customer": c}) for c in (1, 2)], True)
    add("customer_year_revenue", "business", ["invoices"],
        "SELECT SUM(Total) AS revenue FROM invoices WHERE CustomerId=:customer AND InvoiceDate>=:start AND InvoiceDate<:end",
        [(f"客户ID为{c}在2013年的订单金额总和是多少？没有订单保留NULL。", {"customer": c, **year_params(2013)}) for c in (1, 2)], rules=["R01", "R11"])
    add("artist_album_count", "single", ["albums"],
        "SELECT COUNT(*) AS albums FROM albums WHERE ArtistId=:artist",
        [(f"艺术家ID为{a}的专辑共有多少张？", {"artist": a}) for a in (1, 2)])
    add("track_prefix_search", "single", ["tracks"],
        "SELECT TrackId, Name FROM tracks WHERE Name LIKE :prefix ORDER BY TrackId",
        [(f"列出曲目名称以{p}开头的曲目ID和名称，按ID排序。", {"prefix": p + "%"}) for p in ("Love", "Rock")], True)
    add("order_amount_interval", "single", ["invoices"],
        "SELECT COUNT(*) AS orders FROM invoices WHERE Total>=:low AND Total<:high",
        [(f"金额大于等于{lo}且小于{hi}的订单有多少个？", {"low": lo, "high": hi}) for lo, hi in ((1, 5), (5, 10))])
    add("month_order_count", "time", ["invoices"],
        "SELECT COUNT(*) AS orders FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end",
        [(f"2013年{m}月有多少个订单？", {"start": f"2013-{m:02}-01", "end": f"2013-{m+1:02}-01"}) for m in (1, 2)])
    add("manager_direct_reports", "single", ["employees"],
        "SELECT COUNT(*) AS direct_reports FROM employees WHERE ReportsTo=:manager",
        [(f"直属上级ID为{m}的员工有多少人？", {"manager": m}) for m in (1, 2)])
    add("playlist_duration", "join", ["playlist_track", "tracks"],
        "SELECT SUM(t.Milliseconds) AS duration_ms FROM playlist_track pt JOIN tracks t ON t.TrackId=pt.TrackId WHERE pt.PlaylistId=:playlist",
        [(f"播放列表ID为{p}收录曲目的总时长是多少毫秒？", {"playlist": p}) for p in (1, 5)])
    add("playlist_distinct_artists", "complex", ["playlist_track", "tracks", "albums"],
        "SELECT COUNT(DISTINCT al.ArtistId) AS artists FROM playlist_track pt JOIN tracks t ON t.TrackId=pt.TrackId JOIN albums al ON al.AlbumId=t.AlbumId WHERE pt.PlaylistId=:playlist",
        [(f"播放列表ID为{p}收录的曲目涉及多少位不同艺术家？", {"playlist": p}) for p in (1, 5)])
    add("media_type_units", "join", ["tracks", "invoice_items"],
        "SELECT SUM(ii.Quantity) AS units FROM tracks t JOIN invoice_items ii ON ii.TrackId=t.TrackId WHERE t.MediaTypeId=:media",
        [(f"媒体类型ID为{m}的曲目历史售出多少件？", {"media": m}) for m in (1, 2)], rules=["R02"])
    add("country_genre_revenue", "complex", ["genres", "tracks", "invoice_items", "invoices"],
        "SELECT g.GenreId, g.Name, SUM(ii.UnitPrice*ii.Quantity) AS revenue FROM genres g JOIN tracks t ON t.GenreId=g.GenreId JOIN invoice_items ii ON ii.TrackId=t.TrackId JOIN invoices i ON i.InvoiceId=ii.InvoiceId WHERE i.BillingCountry=:country GROUP BY g.GenreId ORDER BY revenue DESC, g.GenreId",
        [(f"账单国家为{c}的已售音乐类型分别贡献多少成交金额？按金额降序、类型ID升序。", {"country": c}) for c in ("USA", "Canada")], True, ["R01", "R03"])
    add("artist_country_revenue", "complex", ["albums", "tracks", "invoice_items", "invoices"],
        "SELECT SUM(ii.UnitPrice*ii.Quantity) AS revenue FROM albums al JOIN tracks t ON t.AlbumId=al.AlbumId JOIN invoice_items ii ON ii.TrackId=t.TrackId JOIN invoices i ON i.InvoiceId=ii.InvoiceId WHERE al.ArtistId=:artist AND i.BillingCountry=:country",
        [(f"艺术家ID为{a}的曲目向账单国家USA贡献多少成交金额？无销售保留NULL。", {"artist": a, "country": "USA"}) for a in (1, 2)], rules=["R01", "R03", "R11"])
    add("customer_last_order", "single", ["invoices"],
        "SELECT MAX(InvoiceDate) AS last_order FROM invoices WHERE CustomerId=:customer",
        [(f"客户ID为{c}最后一次订单的日期是什么？", {"customer": c}) for c in (1, 2)])
    add("monthly_unique_buyers", "time", ["invoices"],
        "SELECT strftime('%Y-%m', InvoiceDate) AS month, COUNT(DISTINCT CustomerId) AS customers FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY month ORDER BY month",
        [(f"{y}年每个有订单月份有多少位不同购买客户，按月份升序？", year_params(y)) for y in years], True)
    add("best_revenue_month", "complex", ["invoices"],
        "SELECT strftime('%Y-%m', InvoiceDate) AS month, SUM(Total) AS revenue FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end GROUP BY month ORDER BY revenue DESC, month LIMIT 1",
        [(f"{y}年销售金额最高的是哪个月，金额多少？并列选月份最早的一个。", year_params(y)) for y in years], True)
    add("annual_growth", "complex", ["invoices"],
        "WITH amounts AS (SELECT SUM(CASE WHEN InvoiceDate>=:start AND InvoiceDate<:end THEN Total ELSE 0 END) AS current_amount, SUM(CASE WHEN InvoiceDate>=:previous_start AND InvoiceDate<:start THEN Total ELSE 0 END) AS previous_amount FROM invoices) SELECT current_amount, previous_amount, (current_amount-previous_amount)*1.0/NULLIF(previous_amount,0) AS growth_rate FROM amounts",
        [(f"{y}年销售金额、上一年金额及同比增长率是多少？增长率用小数，基期为0时返回NULL。", {**year_params(y), "previous_start": f"{y-1}-01-01"}) for y in (2010, 2011)], rules=["R12"])
    add("price_threshold_tracks", "single", ["tracks"],
        "SELECT COUNT(*) AS tracks FROM tracks WHERE UnitPrice>=:price",
        [(f"当前目录标价大于等于{p}的曲目有多少首？", {"price": p}) for p in (0.99, 1.99)], rules=["R04"])
    add("country_top_customers", "join", ["customers", "invoices"],
        "SELECT c.CustomerId, SUM(i.Total) AS revenue FROM customers c JOIN invoices i ON i.CustomerId=c.CustomerId WHERE c.Country=:country GROUP BY c.CustomerId ORDER BY revenue DESC, c.CustomerId LIMIT 3",
        [(f"居住在{c}的客户中累计消费前三名的客户ID和金额，金额并列按客户ID升序取固定3人。", {"country": c}) for c in ("USA", "Canada")], True, ["R03"])
    add("genre_unsold_count", "complex", ["tracks", "invoice_items"],
        "SELECT COUNT(*) AS unsold_tracks FROM tracks t WHERE t.GenreId=:genre AND NOT EXISTS (SELECT 1 FROM invoice_items ii WHERE ii.TrackId=t.TrackId)",
        [(f"类型ID为{g}的曲目中，从未售出的有多少首？", {"genre": g}) for g in (1, 2)])
    add("artist_unsold_albums", "complex", ["albums", "tracks", "invoice_items"],
        "SELECT al.AlbumId, al.Title FROM albums al WHERE al.ArtistId=:artist AND NOT EXISTS (SELECT 1 FROM tracks t JOIN invoice_items ii ON ii.TrackId=t.TrackId WHERE t.AlbumId=al.AlbumId) ORDER BY al.AlbumId",
        [(f"列出艺术家ID为{a}的从未发生销售的专辑ID和标题，按专辑ID升序。", {"artist": a}) for a in (1, 2)], True)
    add("support_customer_country", "join", ["customers"],
        "SELECT Country, COUNT(*) AS customers FROM customers WHERE SupportRepId=:employee GROUP BY Country ORDER BY Country",
        [(f"支持员工ID为{e}负责的客户按居住国家分别有多少人？按国家名排序。", {"employee": e}) for e in (3, 4)], True, ["R10"])
    add("country_year_order_list", "time", ["invoices"],
        "SELECT InvoiceId, InvoiceDate, Total FROM invoices WHERE BillingCountry=:country AND InvoiceDate>=:start AND InvoiceDate<:end ORDER BY InvoiceDate, InvoiceId",
        [(f"列出{y}年账单国家为USA的订单ID、日期和金额，按日期和ID排序。", {"country": "USA", **year_params(y)}) for y in years], True)
    add("duration_interval_tracks", "single", ["tracks"],
        "SELECT COUNT(*) AS tracks FROM tracks WHERE Milliseconds>=:low AND Milliseconds<:high",
        [(f"时长大于等于{lo}且小于{hi}毫秒的曲目有多少首？", {"low": lo, "high": hi}) for lo, hi in ((60000, 120000), (120000, 180000))])
    add("artist_genre_units", "complex", ["albums", "tracks", "invoice_items"],
        "SELECT SUM(ii.Quantity) AS units FROM albums al JOIN tracks t ON t.AlbumId=al.AlbumId JOIN invoice_items ii ON ii.TrackId=t.TrackId WHERE al.ArtistId=:artist AND t.GenreId=:genre",
        [(f"艺术家ID为{a}、类型ID为1的曲目历史售出多少件？无销售保留NULL。", {"artist": a, "genre": 1}) for a in (1, 2)], rules=["R02", "R11"])
    add("genre_weighted_sale_price", "business", ["tracks", "invoice_items"],
        "SELECT SUM(ii.UnitPrice*ii.Quantity)*1.0/NULLIF(SUM(ii.Quantity),0) AS weighted_sale_price FROM tracks t JOIN invoice_items ii ON ii.TrackId=t.TrackId WHERE t.GenreId=:genre",
        [(f"类型ID为{g}的已售曲目，按售出件数加权的平均成交单价是多少？无销售返回NULL。", {"genre": g}) for g in (1, 2)], rules=["R04", "R07"])
    add("year_high_value_orders", "time", ["invoices"],
        "SELECT COUNT(*) AS orders FROM invoices WHERE InvoiceDate>=:start AND InvoiceDate<:end AND Total>=:amount",
        [(f"{y}年金额至少10的订单有多少个？", {**year_params(y), "amount": 10}) for y in years])
    add("country_distinct_sold_tracks", "join", ["invoices", "invoice_items"],
        "SELECT COUNT(DISTINCT ii.TrackId) AS tracks FROM invoice_items ii JOIN invoices i ON i.InvoiceId=ii.InvoiceId WHERE i.BillingCountry=:country",
        [(f"账单国家为{c}的订单历史购买过多少首不同曲目？", {"country": c}) for c in ("USA", "Canada")])
    assert len(result) == 80
    return result
