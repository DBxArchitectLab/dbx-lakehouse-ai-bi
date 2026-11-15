"""DBxLab Medical Supply's synthetic commercial data: price book, inventory and customers.

Deterministic (no randomness): the same date writes the same files, so landing them again is a no-op. The numbers
match the Lakeside fixtures (tests/quote_ai/lakeside_fixtures.py): Gloves A to E in sizes S to XL at $8.60 list,
$6.50 cost and a 12% margin floor; Glove C sizes M and L short (65 each, 500 more on day 14).
SKU IDs are DBxLab's own; AI-03 maps them to real GUDID device identifiers.
"""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

GLOVES = "ABCDE"
SIZES = ["S", "M", "L", "XL"]
TIERS = [("standard", 1, 1.00), ("standard", 200, 0.97), ("contract", 1, 0.95)]
CUSTOMERS = [
    ("ACC-001", "Lakeside Regional Clinic", "standard", "Midwest"),
    ("ACC-002", "Harbor Point Dental Group", "standard", "Northeast"),
    ("ACC-003", "Summit Oncology Center", "contract", "West"),
    ("ACC-004", "Riverbend Veterinary Hospital", "standard", "South"),
]


def _write(path: Path, header: list[str], rows: list[list]) -> Path:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    return path


def dbxlab_synthetic(folder: Path, d: date) -> list[Path]:
    tag = d.strftime("%Y%m%d")
    price_rows, stock_rows = [], []
    for letter in GLOVES:
        for size in SIZES:
            sku = f"GLOVE-{letter}-{size}"
            for tier, min_units, factor in TIERS:
                price_rows.append([f"PB-{tag}", sku, tier, min_units, "box", 8.60, round(8.60 * factor, 2), 6.50,
                                   0.12, d.isoformat()])
            short = letter == "C" and size in ("M", "L")
            stock_rows.append([f"INV-{tag}", sku, 65 if short else 1000, 0, 7, 500 if short else 0,
                               14 if short else "", d.isoformat()])
    return [
        _write(folder / f"price_book_{tag}.csv",
               ["price_book_id", "sku_id", "tier", "min_units", "unit", "list_price", "unit_price", "cost",
                "min_margin", "valid_from"], price_rows),
        _write(folder / f"inventory_{tag}.csv",
               ["snapshot_id", "sku_id", "available", "reserved", "lead_time_days", "replenishment_qty",
                "replenishment_day", "as_of"], stock_rows),
        _write(folder / f"customer_{tag}.csv", ["account_id", "name", "tier", "region"],
               [list(c) for c in CUSTOMERS]),
    ]


GENERATORS = {"dbxlab_synthetic": dbxlab_synthetic}
