"""Deterministic reference audit, with independent Python accounting checks."""
from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from .contracts import ExecutionResult, SQLCandidate
from .evaluation import results_equal, validate_dataset


def audit_dataset(cases, executor, root: Path):
    errors = validate_dataset(cases, executor)
    canonical_families = {}
    checked = 0
    table_errors = []
    for case in cases:
        if case["expected_status"] != "success":
            continue
        candidate = SQLCandidate(case["reference_sql"], case.get("params", {}))
        actual = executor.run(candidate)
        snapshot = case["reference_snapshot"]
        expected = ExecutionResult("ok", snapshot["columns"], snapshot["rows"])
        if not results_equal(actual, expected, case.get("ordered", False)):
            errors.append({"case": case["id"], "error": "Reference snapshot drift"})
        executor.explain(candidate)
        if not executor.last_read_tables.issubset(set(case["required_tables"])):
            table_errors.append(case["id"])
        canonical = re.sub(r"\s+", " ", case["reference_sql"].strip().lower())
        key = canonical
        if key in canonical_families and canonical_families[key] != case["template_family"]:
            errors.append({"case": case["id"], "error": "Identical parameterized SQL assigned different families"})
        canonical_families[key] = case["template_family"]
        checked += 1
    if table_errors:
        errors.append({"error": "Required table annotation misses actual SQL tables", "cases": table_errors})
    with closing(sqlite3.connect(executor.database.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        invoices = [dict(r) for r in conn.execute("SELECT InvoiceId, InvoiceDate, Total, BillingCountry FROM invoices")]
        items = [dict(r) for r in conn.execute("SELECT InvoiceId, TrackId, UnitPrice, Quantity FROM invoice_items")]
        tracks = {r["TrackId"]: dict(r) for r in conn.execute("SELECT TrackId, AlbumId, GenreId FROM tracks")}
        albums = {r["AlbumId"]: r["ArtistId"] for r in conn.execute("SELECT AlbumId, ArtistId FROM albums")}
        artists = {r["ArtistId"]: r["Name"] for r in conn.execute("SELECT ArtistId, Name FROM artists")}
        genres = {r["GenreId"]: r["Name"] for r in conn.execute("SELECT GenreId, Name FROM genres")}
    inv = {i["InvoiceId"]: i for i in invoices}
    country, year, genre_sales, artist_2013, genre_units, month_2013 = (defaultdict(float) for _ in range(6))
    for row in invoices:
        country[row["BillingCountry"]] += row["Total"]
        year[row["InvoiceDate"][:4]] += row["Total"]
        if row["InvoiceDate"].startswith("2013"):
            month_2013[row["InvoiceDate"][:7]] += row["Total"]
    for row in items:
        track = tracks[row["TrackId"]]
        amount = row["UnitPrice"] * row["Quantity"]
        genre_sales[track["GenreId"]] += amount
        genre_units[track["GenreId"]] += row["Quantity"]
        if inv[row["InvoiceId"]]["InvoiceDate"].startswith("2013"):
            artist_2013[albums[track["AlbumId"]]] += amount
    oracles = {
        "S07": [[c, v] for c, v in sorted(country.items(), key=lambda p: (-round(p[1], 2), p[0]))],
        "S08": [[m, value] for m, value in sorted(month_2013.items())],
        "S10": [[g, genres[g], v] for g, v in sorted(genre_sales.items(), key=lambda p: (-round(p[1], 2), p[0]))],
        "S11": [[a, artists[a], v] for a, v in sorted(artist_2013.items(), key=lambda p: (-round(p[1], 2), p[0]))[:5]],
        "S13": [[sum(r["Total"] for r in invoices) / len(invoices)]],
        "S19": [[len({r["InvoiceId"] for r in items}), sum(r["Quantity"] for r in items)]],
        "S20": [[y, amount, sum(r["InvoiceDate"].startswith(y) for r in invoices)] for y, amount in sorted(year.items())],
        "S22": [[g, genres[g], units, rank] for rank, (g, units) in enumerate(sorted(genre_units.items(), key=lambda p: (-p[1], p[0]))[:3], 1)],
        "S28": [[sum(r["UnitPrice"] * r["Quantity"] for r in items)]],
        "S30": [[len({r["TrackId"] for r in items})]],
    }
    total = 0
    cumulative = []
    for month, amount in sorted(month_2013.items()):
        total += amount
        cumulative.append([month, amount, total])
    oracles["S21"] = cumulative
    independent = []
    for case in cases:
        if case["id"] in oracles:
            reference = case["reference_snapshot"]
            correct = results_equal(ExecutionResult("ok", reference["columns"], reference["rows"]),
                                    ExecutionResult("ok", reference["columns"], oracles[case["id"]]), case.get("ordered", False))
            independent.append({"case": case["id"], "independent_python_matches": correct})
            if not correct:
                errors.append({"case": case["id"], "error": "Independent Python aggregation mismatch"})
    # Independent reconciliation across two storage grains.
    reconciles = abs(sum(r["Total"] for r in invoices) - sum(r["UnitPrice"] * r["Quantity"] for r in items)) < 1e-6
    if not reconciles:
        errors.append({"error": "Header/detail totals disagree"})
    result = {"cases": len(cases), "reference_queries_checked": checked, "errors": errors,
              "split_counts": {s: sum(c["split"] == s for c in cases) for s in ("dev", "validation", "test")},
              "template_families": len({c["template_family"] for c in cases}),
              "independent_python_checks": independent, "header_detail_reconciles": reconciles,
              "review": "Agent-authored semantic review + executable checks; not independent human certification",
              "limits": "Exact-template grouping and 11 independent accounting checks do not prove all semantic equivalents were grouped."}
    destination = root / "reports/dataset_audit.json"
    destination.parent.mkdir(exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
