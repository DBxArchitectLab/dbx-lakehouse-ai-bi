"""DBxLab Medical Supply's synthetic product model and history (AI-03). Demo data, deterministic: no real brand, price
or customer. Every row carries demo_data = true.

  products()       the commercial product families: Gloves A to E (the Lakeside tracers) and F, a box of 200
  skus()           one SKU per product and size; catalog numbers like DBX-NE-M-100 (nitrile exam, M, box of 100)
  reps()           the sales team (DBxLab_Sales_Reps, DBxLab_Sales_Managers)
  quote_history()  closed quotes with outcomes from a documented, planted signal (for AI-12 to AI-14)
  bind_devices()   gives each SKU a real GUDID glove device as its supplier record, so the matching has real package
                   levels to work on (a demo binding, documented in docs/quote_ai/data-sources.md)

The price book, inventory and customers are AI-02's (quote_ai.ingest.synthetic); the SKU IDs here are the same.
"""
from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone

SIZES = ["S", "M", "L", "XL"]

# product, name, material, use, code in the catalog number, gloves per box, sellable
_PRODUCTS = [
    ("GLOVE-A", "Glove A, nitrile exam, powder-free", "nitrile", "exam", "NE", 100),
    ("GLOVE-B", "Glove B, nitrile exam, powder-free", "nitrile", "exam", "NB", 100),
    ("GLOVE-C", "Glove C, nitrile exam, powder-free", "nitrile", "exam", "NC", 100),
    ("GLOVE-D", "Glove D, nitrile exam, powder-free", "nitrile", "exam", "ND", 100),
    ("GLOVE-E", "Glove E, nitrile exam, powder-free, contains latex", "nitrile", "exam", "NL", 100),
    ("GLOVE-F", "Glove F, nitrile exam, powder-free, box of 200", "nitrile", "exam", "NE", 200),
]


def products() -> list[dict]:
    """latex_free as DBxLab sells it: Glove E contains natural rubber latex (the excluded tracer)."""
    return [{"product_id": p, "name": n, "material": m, "intended_use": u, "powder_free": True,
             "latex_free": p != "GLOVE-E", "demo_data": True} for p, n, m, u, _, _ in _PRODUCTS]


def skus() -> list[dict]:
    """GLOVE-A-M is DBX-NE-M-100, a box of 100. GLOVE-F-M is DBX-NE-M-200: the same words, "a box", twice the
    gloves. supplier_catalog_number and supplier_di are filled by bind_devices."""
    out = []
    for pid, _, material, use, code, per in _PRODUCTS:
        for s in SIZES:
            out.append({"sku_id": f"{pid}-{s}", "product_id": pid, "catalog_number": f"DBX-{code}-{s}-{per}",
                        "size": s, "material": material, "intended_use": use, "unit": "box",
                        "gloves_per_unit": per, "sellable": True, "supplier_catalog_number": None,
                        "supplier_di": None, "demo_data": True})
    return out


def reps() -> list[dict]:
    return [{"rep_id": "rep.alice", "name": "Alice Rivera", "role": "sales_rep", "region": "Midwest"},
            {"rep_id": "rep.bob", "name": "Bob Chen", "role": "sales_rep", "region": "Northeast"},
            {"rep_id": "mgr.maria", "name": "Maria Okafor", "role": "sales_manager", "region": "Midwest"},
            {"rep_id": "mgr.omar", "name": "Omar Haddad", "role": "sales_manager", "region": "Northeast"}]


def bind_devices(sku_rows: list[dict], devices: Iterable[Mapping]) -> list[dict]:
    """Give each SKU a supplier device: the first unused glove device (by primary DI) with a catalog number and a box
    level of the SKU's size. devices: [{primary_di, catalog_number, box_quantity}]. Deterministic; a SKU with no
    candidate keeps None and so can never be matched or offered."""
    pool = sorted((d for d in devices if d.get("catalog_number")), key=lambda d: str(d["primary_di"]))
    used: set[str] = set()
    out = []
    for sku in sku_rows:
        pick = next((d for d in pool if str(d["primary_di"]) not in used
                     and d.get("box_quantity") == sku["gloves_per_unit"]), None)
        row = dict(sku)
        if pick is not None:
            used.add(str(pick["primary_di"]))
            row.update(supplier_catalog_number=pick["catalog_number"], supplier_di=str(pick["primary_di"]))
        out.append(row)
    return out


# ------------------------------------------------------------------ quote history with a planted signal

SIGNAL = ("p(win) = sigmoid(1.0 - 8 * (unit_price / customer_current_price - 1) - 0.12 * max(lead_time_days - 10, 0) "
          "- 0.9 * (0 if in_stock else 1))")
ACCOUNTS = ["ACC-001", "ACC-002", "ACC-003", "ACC-004"]   # AI-02's synthetic customers
# How each quote's context is drawn (AI-12's generator draws the expired quotes' context from the same ranges)
CURRENT_PRICE_RANGE = (7.6, 9.4)          # what the customer pays today, per box
LEAD_TIMES = [5, 7, 7, 7, 10, 14, 21]     # days we can deliver in
OUT_OF_STOCK_RATE = 0.15


def win_probability(unit_price: float, current_price: float, lead_time_days: int, in_stock: bool) -> float:
    z = 1.0 - 8 * (unit_price / current_price - 1) - 0.12 * max(lead_time_days - 10, 0) - 0.9 * (not in_stock)
    return 1 / (1 + math.exp(-z))


HISTORY_START = datetime(2024, 10, 1, tzinfo=timezone.utc)
HISTORY_DAYS = 730          # two years: October 2024 to September 2026 (AI-12's split tests on July to September 2026)


def quote_history(n: int = 4000, seed: int = 7, start: datetime = HISTORY_START, days: int = HISTORY_DAYS) -> list[dict]:
    """Closed quotes over two years, in time order, each decided after it was quoted (no future leakage).
    Outcomes are drawn from win_probability (SIGNAL): an honest simulation that AI-12 can recover. Sized for Season 3:
    about 125 quotes a month, so a three-month test window measures AUC and calibration above the noise."""
    rng = random.Random(seed)
    sku_ids = [s["sku_id"] for s in skus() if s["product_id"] != "GLOVE-F"]
    out = []
    for i in range(n):
        quoted = start + timedelta(hours=int(i * 24 * days / n) + rng.randint(0, 20))
        current = round(rng.uniform(*CURRENT_PRICE_RANGE), 2)
        price = round(8.60 * rng.choice([1.0, 1.0, 0.97, 0.95, 0.93, 0.90]), 2)
        lead = rng.choice(LEAD_TIMES)
        in_stock = rng.random() > OUT_OF_STOCK_RATE
        p = win_probability(price, current, lead, in_stock)
        won = rng.random() < p
        out.append({"quote_id": f"QH-{i + 1:05d}", "account_id": rng.choice(ACCOUNTS),
                    "rep_id": rng.choice(["rep.alice", "rep.bob"]), "sku_id": rng.choice(sku_ids),
                    "quantity": rng.choice([40, 100, 200, 400, 800]), "unit": "box", "unit_price": price,
                    "customer_current_price": current, "lead_time_days": lead, "in_stock": in_stock,
                    "quoted_at": quoted, "decided_at": quoted + timedelta(days=rng.randint(2, 20)),
                    "outcome": "won" if won else "lost", "demo_data": True})
    return out
