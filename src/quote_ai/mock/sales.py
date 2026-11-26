"""DBxLab's synthetic sales history for the semantic layer (AI-10) and Genie (AI-11). Demo data, deterministic.

  quote_facts()   one row per quote: every closed quote of quote_history (AI-03; two years of it since AI-12; won
                  or lost, the planted signal unchanged), plus quotes that expired, were cancelled or are still
                  open, with the times the metrics need (requested, issued, decided), the list price, the cost and whether a manager had to
                  approve the discount. The source of the metric view quote_ai_sales.quote_metrics.
  order_lines()   one order line per won quote: what each account bought, when and at what price. The source of
                  quote_ai_sales.order_metrics, and of "what has Lakeside bought in the last year?"

The live quotes the app makes (quote_ai_sales.quote, AI-07) join the metrics in AI-20, once the Lakebase-to-Delta
sync for analytics is in place: today they have no outcome yet.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from quote_ai.ingest.synthetic import CUSTOMERS    # AI-02's synthetic accounts (read, never changed here)
from quote_ai.mock import catalog
from quote_ai.rules.policy import PricingPolicy

LIST_PRICE, UNIT_COST = 8.60, 6.50
REP_LIMIT = PricingPolicy().rep_discount_limit         # above this discount a manager approves (policy.yml)
ACCOUNTS = {a: {"account_name": n, "region": r} for a, n, _, r in CUSTOMERS}


def _sku_info() -> dict[str, dict]:
    return {s["sku_id"]: s for s in catalog.skus()}


def _row(q: dict, state: str, outcome: str | None, requested: datetime, issued: datetime | None,
         decided: datetime | None) -> dict:
    sku = _sku_info()[q["sku_id"]]
    acc = ACCOUNTS[q["account_id"]]
    discount = round(1 - q["unit_price"] / LIST_PRICE, 4)
    return {"quote_id": q["quote_id"], "account_id": q["account_id"], "account_name": acc["account_name"],
            "region": acc["region"], "rep_id": q["rep_id"], "sku_id": q["sku_id"],
            "product_family": sku["product_id"], "material": sku["material"], "size": sku["size"],
            "quantity": q["quantity"], "unit": q["unit"], "list_price": LIST_PRICE, "unit_price": q["unit_price"],
            "unit_cost": UNIT_COST, "discount_pct": discount, "approval_needed": discount > REP_LIMIT + 1e-9,
            "state": state, "outcome": outcome, "requested_at": requested, "issued_at": issued,
            "decided_at": decided, "demo_data": True}


def quote_facts(seed: int = 11) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for q in catalog.quote_history():
        requested = q["quoted_at"] - timedelta(hours=rng.choice([2, 4, 6, 8, 12, 20, 26, 30, 48, 72]))
        out.append(_row(q, "ISSUED", q["outcome"].upper(), requested, q["quoted_at"], q["decided_at"]))
    # quotes that never closed: expired (issued, no answer), cancelled, and open ones in the last weeks
    # expired and cancelled quotes spread evenly over the two years of history, so the win rate (and AI-12's label)
    # has no step in it
    start = catalog.HISTORY_START + timedelta(days=7)
    span = catalog.HISTORY_DAYS - 30
    skus = [s["sku_id"] for s in catalog.skus() if s["product_id"] != "GLOVE-F"]
    for i in range(130):
        kind = "EXPIRED" if i < 70 else "CANCELLED" if i < 100 else "OPEN"
        k, of = (i, 70) if kind == "EXPIRED" else (i - 70, 30)
        requested = (datetime(2026, 11, 2, tzinfo=timezone.utc) + timedelta(hours=7 * (i - 100))
                     if kind == "OPEN" else start + timedelta(hours=int(k * 24 * span / of) + rng.randint(0, 20)))
        price = round(LIST_PRICE * rng.choice([1.0, 0.97, 0.95, 0.93, 0.90, 0.86]), 2)
        q = {"quote_id": f"QX-{i + 1:05d}", "account_id": rng.choice(catalog.ACCOUNTS),
             "rep_id": rng.choice(["rep.alice", "rep.bob"]), "sku_id": rng.choice(skus),
             "quantity": rng.choice([40, 100, 200, 400]), "unit": "box", "unit_price": price}
        issued = requested + timedelta(hours=rng.choice([4, 8, 24, 36]))
        if kind == "EXPIRED":
            out.append(_row(q, "EXPIRED", "EXPIRED", requested, issued, issued + timedelta(days=30)))
        elif kind == "CANCELLED":
            out.append(_row(q, "CANCELLED", None, requested, None, None))
        else:
            state = rng.choice(["DRAFT", "PRICED", "PENDING_APPROVAL"])
            out.append(_row(q, state, None, requested, None, None))
    return out


def order_lines(facts: list[dict] | None = None, seed: int = 12) -> list[dict]:
    """A won quote becomes an order one to five days after the customer said yes."""
    rng = random.Random(seed)
    out = []
    for i, q in enumerate(f for f in (facts or quote_facts()) if f["outcome"] == "WON"):
        out.append({"order_id": f"SO-{i + 1:05d}", "line_no": 1, "quote_id": q["quote_id"],
                    "account_id": q["account_id"], "rep_id": q["rep_id"], "sku_id": q["sku_id"],
                    "order_date": (q["decided_at"] + timedelta(days=rng.randint(1, 5))).date(),
                    "quantity": q["quantity"], "unit": q["unit"], "unit_price": q["unit_price"],
                    "list_price": q["list_price"], "unit_cost": q["unit_cost"], "demo_data": True})
    return out
