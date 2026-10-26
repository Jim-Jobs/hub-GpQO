from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import deque
from contextlib import closing
from pathlib import Path
from typing import Any

CATALOG_VERSION = 1
DESCRIPTIONS = {
    "artists": "艺术家", "albums": "专辑", "tracks": "曲目目录和当前标价，不是播放记录",
    "genres": "音乐类型", "media_types": "媒体类型", "playlists": "播放列表",
    "playlist_track": "播放列表收录关系，不是播放次数", "customers": "客户及其支持员工",
    "employees": "员工和直属上级，不能直接当作销售人员", "invoices": "订单头、日期、账单地址、订单总金额",
    "invoice_items": "订单明细、历史成交单价和数量",
}
SAMPLE_COLUMNS = {"Name", "Title", "Country", "BillingCountry", "InvoiceDate"}


def fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_database(path: Path, cache_dir: Path | None = None) -> dict[str, Any]:
    """Read a trusted local DB; no customer phone/email/address samples go to the model."""
    path = path.resolve(strict=True)
    digest = fingerprint(path)
    cache = cache_dir / f"catalog-{digest[:16]}-v{CATALOG_VERSION}.json" if cache_dir else None
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        names = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
        tables = {}
        for name in names:
            columns = []
            count = conn.execute(f"SELECT COUNT(*) FROM {quote(name)}").fetchone()[0]
            for _, col, dtype, required, default, primary_key in conn.execute(f"PRAGMA table_info({quote(name)})"):
                qcol = quote(col)
                profile = conn.execute(
                    f"SELECT COUNT(DISTINCT {qcol}), SUM({qcol} IS NULL) FROM {quote(name)}"
                ).fetchone()
                column = {"name": col, "type": dtype, "nullable": not bool(required or primary_key),
                          "primary_key": bool(primary_key), "distinct": profile[0], "nulls": profile[1] or 0}
                if col in SAMPLE_COLUMNS:
                    column["samples"] = [r[0] for r in conn.execute(
                        f"SELECT DISTINCT {qcol} FROM {quote(name)} WHERE {qcol} IS NOT NULL ORDER BY {qcol} LIMIT 3"
                    )]
                if "Date" in col:
                    column["range"] = list(conn.execute(
                        f"SELECT MIN({qcol}), MAX({qcol}) FROM {quote(name)}"
                    ).fetchone())
                columns.append(column)
            foreign_keys = [{"from_table": name, "from_column": r[3], "to_table": r[2], "to_column": r[4]}
                            for r in conn.execute(f"PRAGMA foreign_key_list({quote(name)})")]
            indexes = [{"name": r[1], "unique": bool(r[2])} for r in conn.execute(f"PRAGMA index_list({quote(name)})")]
            tables[name] = {"description": DESCRIPTIONS.get(name, ""), "row_count": count,
                            "columns": columns, "foreign_keys": foreign_keys, "indexes": indexes}
    catalog = {"version": CATALOG_VERSION, "database_sha256": digest, "tables": tables}
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
    return catalog


def compact_catalog(catalog: dict) -> dict:
    return {name: {"description": t["description"], "columns": [c["name"] for c in t["columns"]],
                   "foreign_keys": t["foreign_keys"]} for name, t in catalog["tables"].items()}


def complete_bridges(tables: list[str], catalog: dict) -> tuple[list[str], list[str]]:
    """Suggest FK bridge tables; caller still checks business grain/relationship semantics."""
    graph: dict[str, set[str]] = {n: set() for n in catalog["tables"]}
    for name, info in catalog["tables"].items():
        for fk in info["foreign_keys"]:
            graph[name].add(fk["to_table"])
            graph[fk["to_table"]].add(name)
    unknown = set(tables) - graph.keys()
    if unknown:
        raise ValueError(f"Unknown tables: {sorted(unknown)}")
    selected = list(dict.fromkeys(tables))
    bridges = []
    if not selected:
        raise ValueError("Table selection is empty")
    connected = {selected[0]}
    for target in selected[1:]:
        queue = deque((node, [node]) for node in sorted(connected))
        seen = set(connected)
        route = None
        while queue:
            node, route_so_far = queue.popleft()
            if node == target:
                route = route_so_far
                break
            for neighbor in sorted(graph[node] - seen):
                seen.add(neighbor)
                queue.append((neighbor, route_so_far + [neighbor]))
        if route is None:
            raise ValueError(f"No foreign-key route to {target}")
        for node in route:
            if node not in selected and node not in bridges:
                bridges.append(node)
        connected.update(route)
    return selected + bridges, bridges
