"""QuoteAI v1's quick quote (AI-07): a first, rough answer by simple rules, clearly labelled. Replaced step by step:
AI-15 (eligibility and fulfilment), AI-16 (comparison), AI-18 (price_quote), AI-21 (discount bands and approval).

  exact_match  the gloves whose catalog attributes match exactly (material, use, powder-free, latex-free) and that
               have, for every quote line, a sellable SKU in that size and unit, with the confirmed glove count,
               through a verified mapping. What v1 can't check (chemo testing, sterility, delivery) is
               "not checked in v1", never a pass.
  list_price   the price book's list price for the selling unit, per line; no tiers, no customer price, no discount.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from quote_ai.domain.models import QuoteLine, Requirements
from quote_ai.rules.pricing import PriceBookRow

NOT_CHECKED = "not checked in v1"
MATCHED = "matched"
NOT_STATED = "not stated"
PRICE_METHOD = "v1_list_price"
LABEL = "v1: simple rules, demo data"


class CatalogSku(BaseModel):
    """One sellable SKU with its product's catalog attributes, as the app reads it (quote_ai_product)."""
    sku_id: str
    product_id: str
    product_name: str = ""
    catalog_number: str | None = None
    size: str
    unit: str
    gloves_per_unit: int
    sellable: bool
    mapping_verified: bool
    material: str | None = None
    intended_use: str | None = None
    powder_free: bool | None = None
    latex_free: bool | None = None


class Match(BaseModel):
    product_id: str
    product_name: str = ""
    skus: dict[str, str]                       # size -> sku_id
    checks: dict[str, str] = Field(default_factory=dict)


def _attribute_checks(req: Requirements, sku: CatalogSku) -> dict[str, str] | None:
    """The catalog attributes v1 compares exactly; None when one of them doesn't match."""
    checks: dict[str, str] = {}
    for name, wanted, have in (("material", req.material, sku.material),
                               ("intended_use", req.intended_use, sku.intended_use)):
        if wanted is None:
            checks[name] = NOT_STATED
        elif wanted != have:
            return None
        else:
            checks[name] = MATCHED
    for name, wanted, have in (("powder_free", req.powder_free, sku.powder_free),
                               ("latex_free", req.latex_free, sku.latex_free)):
        if wanted:
            if have is not True:
                return None
            checks[name] = MATCHED
    if req.chemo_tested:
        checks["chemo_tested"] = NOT_CHECKED
    if req.sterile:
        checks["sterile"] = NOT_CHECKED
    if req.delivery_days is not None:
        checks["delivery"] = NOT_CHECKED
    return checks


def exact_match(reqs: Requirements, lines: list[QuoteLine], catalog: list[CatalogSku]) -> list[Match]:
    """Every product that matches exactly, with its SKU for each line. A SKU without a verified mapping, or whose
    unit or glove count differs from what the rep confirmed, never matches."""
    by_product: dict[str, list[CatalogSku]] = {}
    for s in catalog:
        by_product.setdefault(s.product_id, []).append(s)
    out = []
    for pid in sorted(by_product):
        skus = by_product[pid]
        checks = _attribute_checks(reqs, skus[0])
        if checks is None:
            continue
        chosen: dict[str, str] = {}
        for line in lines:
            sku = next((s for s in skus if s.size == line.size and s.sellable and s.mapping_verified
                        and s.unit == line.unit and s.gloves_per_unit == reqs.gloves_per_unit), None)
            if sku is None:
                break
            chosen[line.size] = sku.sku_id
        if len(chosen) == len(lines) and lines:
            out.append(Match(product_id=pid, product_name=skus[0].product_name, skus=chosen,
                             checks=checks | {"sizes": MATCHED, "unit": MATCHED}))
    return out


def list_price(skus: dict[str, str], lines: list[QuoteLine], rows: list[PriceBookRow]) -> list[PriceBookRow]:
    """The list-price row for each line's SKU: the standard tier's first break (min_units 1). Raises if a SKU has
    none: v1 never invents a price."""
    out = []
    for line in lines:
        sku_id = skus[line.size]
        row = next((r for r in rows if r.sku_id == sku_id and r.tier == "standard" and r.min_units == 1), None)
        if row is None:
            raise ValueError(f"no list price for {sku_id}")
        out.append(row)
    return out
