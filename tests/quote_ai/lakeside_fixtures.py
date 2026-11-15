"""The Lakeside transaction and the five tracer gloves (plan 3.7, 4.6), as test data. Demo data, synthetic.

Glove A  eligible; chemo verified from its supplier report; ships in full by day 7.
Glove B  the same claims, but its chemo claim is only extracted text until its report arrives (snapshot ev-57).
Glove C  eligible, but M and L ship 250 now and 150 in three weeks: an alternative that needs acceptance.
Glove D  eligible; ships in full.
Glove E  excluded: contains natural rubber latex.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from quote_ai.domain.models import Evidence, Product, Requirements, Sku, SourceType as ST, Verification as V
from quote_ai.rules.fulfilment import Stock
from quote_ai.rules.pricing import PriceBookRow

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
SIZES = ["S", "M", "L", "XL"]
LAKESIDE_SPLIT = {"S": 60, "M": 140, "L": 140, "XL": 60}   # boxes of 100, confirmed by the rep (r2)
GLOVES = "ABCDE"

LAKESIDE_REQUEST = ("Lakeside Regional Clinic needs 400 boxes of powder-free nitrile exam gloves, chemo-tested, "
                    "latex-free, sizes S to XL, non-sterile is fine. They pay about $8 a box today for Brand X and "
                    "need delivery in 10 days.")


def lakeside_r1() -> Requirements:
    """As extracted (AI-05): every stated requirement, plus what the request leaves open."""
    return Requirements(version=1, material="nitrile", powder_free=True, intended_use="exam", latex_free=True,
                        chemo_tested=True, sterile=None, sizes=SIZES, quantity=400, unit="box",
                        target_unit_price=8.00, delivery_days=10, missing=["quantity_per_size", "gloves_per_unit"])


def product(letter: str, mapping_verified: bool = True) -> Product:
    pid = f"GLOVE-{letter}"
    return Product(product_id=pid, name=f"Glove {letter}", skus=[
        Sku(sku_id=f"{pid}-{s}", product_id=pid, size=s, unit="box", gloves_per_unit=100, sellable=True,
            mapping_verified=mapping_verified) for s in SIZES])


def _row(pid: str, attribute: str, value, source: ST = ST.GUDID, verification: V = V.VERIFIED,
         source_id: str | None = None) -> Evidence:
    return Evidence(product_id=pid, attribute=attribute, value=value, source_type=source,
                    source_id=source_id or f"{source.value}:{pid}:{attribute}", verification=verification)


def evidence(snapshot: str = "ev-41") -> list[Evidence]:
    """Evidence as of a snapshot. ev-41: before Glove B's report. ev-57: Glove B's report verified (AI-26)."""
    rows: list[Evidence] = []
    for letter in GLOVES:
        pid = f"GLOVE-{letter}"
        rows += [_row(pid, "material", "nitrile", ST.SUPPLIER_DOCUMENT, source_id=f"SPEC-{letter}"),
                 _row(pid, "powder_free", True, ST.SUPPLIER_DOCUMENT, source_id=f"SPEC-{letter}"),
                 _row(pid, "intended_use", "exam"),                       # FDA product code
                 _row(pid, "latex_free", letter != "E"),                  # GUDID NRL labeling
                 _row(pid, "in_commercial_distribution", True),
                 # every description claims chemo testing: an extracted claim, never enough on its own
                 _row(pid, "chemo_tested", True, ST.AI_EXTRACTION, V.UNVERIFIED, source_id=f"DESC-{letter}")]
        if letter in "ACD" or (letter == "B" and snapshot == "ev-57"):
            rows.append(_row(pid, "chemo_tested", True, ST.SUPPLIER_DOCUMENT, V.VERIFIED,
                             source_id=f"RPT-CHEMO-{letter}"))
    return rows


def stock() -> dict[str, Stock]:
    out = {}
    for letter in GLOVES:
        for s in SIZES:
            sku = f"GLOVE-{letter}-{s}"
            if letter == "C" and s in ("M", "L"):     # 65 + 65 now; the other 150 in three weeks
                out[sku] = Stock(sku_id=sku, available=65, lead_time_days=7, replenishment_qty=500,
                                 replenishment_day=14)
            else:
                out[sku] = Stock(sku_id=sku, available=1000, lead_time_days=7)
    return out


def price_book() -> list[PriceBookRow]:
    return [PriceBookRow(id=f"PB-{letter}-{s}", sku_id=f"GLOVE-{letter}-{s}", tier="standard", min_units=1,
                         list_price=8.60, unit_price=8.60, cost=6.50, min_margin=0.12)
            for letter in GLOVES for s in SIZES]


def fresh_inputs(age: timedelta = timedelta(hours=2)) -> dict[str, datetime]:
    return {"gudid": NOW - age, "evidence": NOW - age, "inventory": NOW - age, "price_book": NOW - age}
