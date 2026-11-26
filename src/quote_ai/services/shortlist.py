"""The shortlist (AI-15): search finds candidates, the rules decide, QuoteService commits.

  build_shortlist   the confirmed requirements as a query -> candidate products (AI Search, filters narrow only) ->
                    eligibility per candidate -> fulfilment per eligible product. Decides nothing itself.
  LocalRules        the rules in this process (tests, the local demo, the evaluation suites)
  SqlRules          the same rules as Unity Catalog functions through the app's SQL warehouse
                    (SELECT {ai}.check_eligibility(...), {ai}.check_fulfilment(...)): the app, SQL and, from AI-18,
                    the agent get the same answer

The route (app/backend/routers/shortlist.py) calls build_shortlist and then QuoteService.shortlist_candidates, the
only path to SHORTLISTED.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from quote_ai.domain.models import Eligibility, EligibilityResult, FulfilmentResult, Product, QuoteLine, Requirements
from quote_ai.product.evidence import rows_to_domain
from quote_ai.rules import eligibility, fulfilment, uc
from quote_ai.search import retriever


class Rules(Protocol):
    def eligibility(self, products: list[Product], req: Requirements) -> tuple[list[EligibilityResult], str]: ...
    def fulfilment(self, products: list[Product], lines: list[QuoteLine],
                   delivery_days: int) -> tuple[dict[str, FulfilmentResult], str]: ...


class LocalRules:
    """quote_ai.rules in this process; evidence and stock from the app's stores."""

    def __init__(self, evidence_store, catalog) -> None:
        self.evidence, self.catalog = evidence_store, catalog

    def eligibility(self, products, req):
        snapshot = self.evidence.snapshot()
        ev = rows_to_domain(self.evidence.for_products([p.product_id for p in products]))
        return [eligibility.check_product(p, req, ev, snapshot) for p in products], snapshot

    def fulfilment(self, products, lines, delivery_days):
        stock, snapshot = self.catalog.stock([s.sku_id for p in products for s in p.skus])
        return {p.product_id: fulfilment.check_product(p, lines, stock, delivery_days, snapshot)
                for p in products}, snapshot


class SqlRules:
    """The Unity Catalog functions through a SQL warehouse (quote_ai.storage.delta_repository SqlExecutor).
    names: quote_ai.storage.sql_files names (names['ai'] is catalog.schema of quote_ai_ai). The evidence snapshot
    is read just before (SqlEvidenceStore.snapshot)."""

    def __init__(self, sql, names: dict[str, str], evidence_store) -> None:
        self.sql, self.n, self.evidence = sql, names, evidence_store

    def eligibility(self, products, req):
        snapshot = self.evidence.snapshot()
        out = []
        for p in products:
            rows = self.sql.query(f"SELECT {self.n['ai']}.check_eligibility(:sku, :req) AS r",
                                  {"sku": p.skus[0].sku_id, "req": req.model_dump_json()})
            r = uc.eligibility_result(rows[0]["r"])
            out.append(r.model_copy(update={"evidence_snapshot": snapshot}))
        return out, snapshot

    def fulfilment(self, products, lines, delivery_days):
        out, snapshot = {}, ""
        payload = json.dumps([l.model_dump(include={"line_no", "size", "quantity", "unit"}) for l in lines])
        for p in products:
            rows = self.sql.query(f"SELECT {self.n['ai']}.check_fulfilment(:sku, :lines, :days) AS r",
                                  {"sku": p.skus[0].sku_id, "lines": payload, "days": int(delivery_days)})
            r = uc.fulfilment_result(rows[0]["r"])
            out[p.product_id], snapshot = r, r.inventory_snapshot
        return out, snapshot


@dataclass
class Shortlist:
    query: str
    candidates: list[str]                                   # product IDs in rank order
    products: dict[str, Product]
    results: list[EligibilityResult]
    fulfilment: dict[str, FulfilmentResult] = field(default_factory=dict)
    evidence_snapshot: str = ""
    inventory_snapshot: str = ""

    def group(self, status: Eligibility) -> list[EligibilityResult]:
        return [r for r in self.results if r.status == status]


def build_shortlist(req: Requirements, lines: list[QuoteLine], index, catalog, rules: Rules, k: int = 40,
                    max_products: int = 8, query_type: str = "HYBRID", rerank: bool = False) -> Shortlist:
    query = retriever.need_text(req)
    hits = retriever.search_candidates(index, query, req.sizes, k=k, query_type=query_type, rerank=rerank)
    ids = retriever.candidate_products(hits, max_products)
    products = {p.product_id: p for p in catalog.products(ids)}
    ordered = [products[i] for i in ids if i in products]
    results, ev_snap = rules.eligibility(ordered, req)
    eligible = [products[r.product_id] for r in results if r.status == Eligibility.ELIGIBLE]
    ful, inv_snap = rules.fulfilment(eligible, lines, req.delivery_days or 0) if eligible else ({}, "")
    return Shortlist(query, [p.product_id for p in ordered], products, results, ful, ev_snap, inv_snap)


def view(s: Shortlist) -> dict:
    """The three groups the Shortlist screen shows: eligible (with fulfilment), needs evidence, excluded (with the
    check that failed). Every check carries its state; unknown is never drawn as a pass."""
    def one(r: EligibilityResult) -> dict:
        p = s.products[r.product_id]
        out = {"product_id": r.product_id, "name": p.name, "status": r.status.value,
               "checks": [c.model_dump(mode="json") for c in r.checks],
               "failed": [c.requirement for c in r.checks if c.result.value != "verified_match"]}
        if r.product_id in s.fulfilment:
            out["fulfilment"] = fulfilment.summary(s.fulfilment[r.product_id])
        return out
    return {"query": s.query, "candidates": s.candidates, "evidence_snapshot": s.evidence_snapshot,
            "inventory_snapshot": s.inventory_snapshot,
            "eligible": [one(r) for r in s.group(Eligibility.ELIGIBLE)],
            "needs_evidence": [one(r) for r in s.group(Eligibility.NEEDS_EVIDENCE)],
            "excluded": [one(r) for r in s.group(Eligibility.EXCLUDED)]}
