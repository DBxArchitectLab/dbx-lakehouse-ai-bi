"""The rules as Unity Catalog functions (AI-15): JSON in, JSON out, the same Python underneath.

A Unity Catalog Python function cannot read a table, so each rule is two functions (src/quote_ai/rules/sql/):

  {ai}.eligibility_rules(requirements_json, skus_json, evidence_json, snapshot)   Python: calls eligibility_json
  {ai}.check_eligibility(sku_id, requirements_json)                               SQL: gathers the product's SKUs
                                                                                  and evidence, calls the above
  {ai}.fulfilment_rules(skus_json, lines_json, stock_json, delivery_days, snapshot)   Python: fulfilment_json
  {ai}.check_fulfilment(sku_id, lines_json, delivery_days)                        SQL: the latest inventory snapshot

The Python functions import this module from the quote_ai wheel (ENVIRONMENT dependencies), so SQL, the app, the
notebooks and, from AI-18, the agent's tools run the code tests/quote_ai/test_eligibility.py checks. One
implementation, many callers.
"""
from __future__ import annotations

import json

from quote_ai.domain.models import EligibilityResult, FulfilmentResult, Product, QuoteLine, Requirements, Sku
from quote_ai.product.evidence import rows_to_domain
from quote_ai.rules import eligibility, fulfilment


def _product(skus: list[dict]) -> Product:
    if not skus:
        raise ValueError("no SKU found for this product")
    pid = skus[0]["product_id"]
    return Product(product_id=pid, name=skus[0].get("product_name") or "", skus=[
        Sku(sku_id=s["sku_id"], product_id=s["product_id"], size=s["size"], unit=s["unit"],
            gloves_per_unit=int(s["gloves_per_unit"]), sellable=bool(s["sellable"]),
            mapping_verified=bool(s["mapping_verified"])) for s in skus])


def _load(text: str | None, default):
    return json.loads(text) if text else default


def eligibility_json(requirements_json: str, skus_json: str, evidence_json: str, evidence_snapshot: str) -> str:
    """One product's eligibility. requirements_json: a quote_ai Requirements (r2); skus_json: catalog_skus rows of
    the product; evidence_json: its eligibility_evidence rows. Returns an EligibilityResult as JSON. A malformed
    input raises: a function that fails is better than one that guesses."""
    req = Requirements.model_validate_json(requirements_json)
    product = _product(_load(skus_json, []))
    evidence = rows_to_domain(_load(evidence_json, []))
    r = eligibility.check_product(product, req, evidence, evidence_snapshot)
    return r.model_dump_json()


def fulfilment_json(skus_json: str, lines_json: str, stock_json: str, delivery_days: int,
                    inventory_snapshot: str) -> str:
    """One product's fulfilment for the quote lines (lines_json: [{line_no, size, quantity, unit}]); stock_json:
    inventory_snapshot rows of its SKUs. Returns a FulfilmentResult as JSON, with the summary the screen shows."""
    product = _product(_load(skus_json, []))
    lines = [QuoteLine.model_validate(l) for l in _load(lines_json, [])]
    stock = {s["sku_id"]: fulfilment.Stock.model_validate({k: v for k, v in s.items() if v is not None
                                                           and k in fulfilment.Stock.model_fields})
             for s in _load(stock_json, [])}
    r = fulfilment.check_product(product, lines, stock, int(delivery_days), inventory_snapshot)
    return json.dumps(r.model_dump(mode="json") | {"summary": fulfilment.summary(r)})


def eligibility_result(text: str) -> EligibilityResult:
    return EligibilityResult.model_validate_json(text)


def fulfilment_result(text: str) -> FulfilmentResult:
    d = json.loads(text)
    d.pop("summary", None)
    return FulfilmentResult.model_validate(d)


def price_json(book_json: str, packaging_json: str, lines_json: str, account_tier: str) -> str:
    """The quote's lines priced by rule (AI-18, quote_ai_ai.price_quote): book_json, the product's price book rows;
    packaging_json, catalog_skus rows (sku_id, size, unit, gloves_per_unit); lines_json, the quote lines
    [{size, quantity, unit}]. Returns {"lines": [...], "total": ...} or {"refused": reason}: a PricingError is an
    answer, never a guess."""
    from quote_ai.rules import pricing
    from quote_ai.storage.product_catalog import sku_packaging
    book = [pricing.PriceBookRow.model_validate(r) for r in _load(book_json, [])]
    skus = {s["size"]: s for s in _load(packaging_json, [])}
    pack = {s["sku_id"]: sku_packaging(s["unit"], s["gloves_per_unit"]) for s in skus.values()}
    try:
        lines = []
        for l in _load(lines_json, []):
            if l["size"] not in skus:
                raise pricing.PricingError(f"no SKU in size {l['size']}")
            lines.append(pricing.price_quote(skus[l["size"]]["sku_id"], int(l["quantity"]), l["unit"], account_tier,
                                             book, pack) | {"size": l["size"]})
    except pricing.PricingError as e:
        return json.dumps({"refused": str(e)})
    return json.dumps({"lines": lines, "total": round(sum(l["total"] for l in lines), 2)})
