"""DBxLab's synthetic quote history for win prediction (AI-12 to AI-14). Demo data, deterministic: it composes the
existing mock modules and never changes them, so the metric views (AI-10) and Genie (AI-11) keep their numbers.

  quote_history()           every decided quote (WON, LOST, EXPIRED; the label set of ADR 0003) with what was known
                            when it was sent (sent_at): our price, the list price, the customer's current price, the
                            lead time and the stock; and what was only known afterwards (decided_at, the outcome,
                            final_negotiated_price, days_to_decision). quote_ai_sales.quote_history
  customer_price_history()  what each customer pays today per box, observed when each quote was prepared (one hour
                            before sent_at), plus today's observation. quote_ai_sales.customer_price_history
  inventory_history()       the quoted SKU's stock and lead time, snapshot one hour before sent_at.
                            quote_ai_sales.inventory_history (today's stock is AI-02's ingested inventory)
  drift_month()             AI-14's demo: a month where competitors cut prices and customers' current prices fall
  generate()                all of the above, by table name

THE PLANTED SIGNAL (documented in docs/quote_ai/ai-12-ml-features/README.md, checked in AI-13):
  p(win) = sigmoid(1.0 - 8 * (unit_price / customer_current_price - 1) - 0.12 * max(lead_time_days - 10, 0)
                   - 0.9 * (0 if in_stock else 1))                                   (quote_ai.mock.catalog.SIGNAL)
Won and lost quotes come from quote_ai.mock.catalog.quote_history, drawn from that signal. Expired quotes (a quote
the customer let lapse counts as lost, ADR 0003) come from quote_ai.mock.sales; their context is drawn from the same
ranges and kept only where the signal says "lost", so an expired quote is a silent loss under the same signal.
An educational simulation: it teaches the method and lets AI-13 check that a model finds the signal. It is not
evidence that win prediction works for a real sales team.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from quote_ai.mock import catalog, sales

TODAY = datetime(2026, 11, 24, tzinfo=timezone.utc)   # "now" for the demo data; fixed, so every run is identical
DAILY_BOXES = 40                                      # average boxes a SKU sells per day: stock cover = stock / this
PREPARED_BEFORE = timedelta(hours=1)                  # the rep checks price and stock an hour before sending
# What each customer pays per box today (Lakeside: $8.00, the price it asks for in the Lakeside request)
CURRENT_PRICE_TODAY = {"ACC-001": 8.00, "ACC-002": 8.40, "ACC-003": 8.90, "ACC-004": 8.20}


def _stock(rng: random.Random, quantity: int, in_stock: bool) -> int:
    """Boxes available when the quote was prepared: in stock covers the quantity, out of stock does not."""
    return quantity + rng.randint(200, 1200) if in_stock else rng.randint(0, max(quantity // 4, 1))


def _expired_context(rng: random.Random, unit_price: float) -> tuple[float, int, bool]:
    """Draw the context of an expired quote from the history's ranges until the planted signal says lost."""
    while True:
        current = round(rng.uniform(*catalog.CURRENT_PRICE_RANGE), 2)
        lead = rng.choice(catalog.LEAD_TIMES)
        in_stock = rng.random() > catalog.OUT_OF_STOCK_RATE
        if rng.random() >= catalog.win_probability(unit_price, current, lead, in_stock):
            return current, lead, in_stock


def quote_history(seed: int = 21) -> list[dict]:
    """Every decided quote, in time order. Columns known at sent_at, then columns known only after the outcome."""
    rng = random.Random(seed)
    closed = {q["quote_id"]: q for q in catalog.quote_history()}
    out = []
    for f in sales.quote_facts():
        if f["outcome"] not in ("WON", "LOST", "EXPIRED") or f["issued_at"] is None:
            continue
        h = closed.get(f["quote_id"])
        if h is not None:
            current, lead, in_stock = h["customer_current_price"], h["lead_time_days"], h["in_stock"]
        else:
            current, lead, in_stock = _expired_context(rng, f["unit_price"])
        won = f["outcome"] == "WON"
        # The leak: the price the customer finally paid exists only once they said yes (a small concession, or none)
        final = round(f["unit_price"] - rng.choice([0, 0, 0.05, 0.10]), 2) if won else None
        out.append({"quote_id": f["quote_id"], "account_id": f["account_id"], "account_name": f["account_name"],
                    "rep_id": f["rep_id"], "sku_id": f["sku_id"], "quantity": f["quantity"], "unit": f["unit"],
                    "unit_price": f["unit_price"], "list_price": f["list_price"], "customer_current_price": current,
                    "lead_time_days": lead, "stock_available": _stock(rng, f["quantity"], in_stock),
                    "in_stock": in_stock, "sent_at": f["issued_at"],
                    "decided_at": f["decided_at"], "outcome": f["outcome"], "final_negotiated_price": final,
                    "days_to_decision": (f["decided_at"] - f["issued_at"]).days, "demo_data": True})
    return sorted(out, key=lambda r: r["sent_at"])


def customer_price_history(history: list[dict] | None = None) -> list[dict]:
    """One observation per quote (prepared an hour before it was sent), plus today's per account."""
    rows = [{"account_id": q["account_id"], "observed_at": q["sent_at"] - PREPARED_BEFORE,
             "current_price": q["customer_current_price"], "source": "quote preparation", "demo_data": True}
            for q in (history if history is not None else quote_history())]
    rows += [{"account_id": a, "observed_at": TODAY, "current_price": p, "source": "account review",
              "demo_data": True} for a, p in CURRENT_PRICE_TODAY.items()]
    return sorted(rows, key=lambda r: (r["account_id"], r["observed_at"]))


def inventory_history(history: list[dict] | None = None) -> list[dict]:
    """The quoted SKU's stock and lead time, an hour before each quote was sent."""
    rows = [{"sku_id": q["sku_id"], "as_of": q["sent_at"] - PREPARED_BEFORE, "available": q["stock_available"],
             "lead_time_days": q["lead_time_days"], "demo_data": True}
            for q in (history if history is not None else quote_history())]
    return sorted(rows, key=lambda r: (r["sku_id"], r["as_of"]))


def drift_month(end: datetime = TODAY + timedelta(days=1), drop: float = 0.08, seed: int = 31) -> list[dict]:
    """AI-14's injected drift: over the 30 days up to `end` (now, in the demo) competitors cut prices, so every
    customer's current price falls by about `drop`, the lowest observation last. New rows for
    quote_ai_sales.customer_price_history (source 'drift demo'); delete them to undo."""
    rng = random.Random(seed)
    rows = []
    for day in range(0, 30, 3):
        for a, p in CURRENT_PRICE_TODAY.items():
            factor = 1 - drop * (day + 3) / 30 * rng.uniform(0.9, 1.0)
            rows.append({"account_id": a, "observed_at": end - timedelta(days=27 - day, minutes=5),
                         "current_price": round(p * factor, 2), "source": "drift demo", "demo_data": True})
    return rows


def generate() -> dict[str, list[dict]]:
    history = quote_history()
    return {"quote_history": history, "customer_price_history": customer_price_history(history),
            "inventory_history": inventory_history(history)}
