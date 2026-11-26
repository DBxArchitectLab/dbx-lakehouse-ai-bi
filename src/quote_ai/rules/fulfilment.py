"""Fulfilment feasibility, per quote line: can DBxLab ship it by the requested date?

A separate decision from eligibility (AI-15). It runs only for eligible products and reads business truth only: the
inventory snapshot (available, reserved, replenishment, lead time). Four answers per line:

  full              enough free stock now
  partial           some now, the rest from the next receipt (how many, by when, and whether that is late)
  backorder         nothing now, the next receipt covers it all (or no date is known at all)
  alternative_date  the next receipt doesn't cover it: the earliest day it can all ship (earliest_full_day)

A product that misses a hard delivery date is not excluded; it is an alternative that needs the customer's
acceptance (plan 4.1). Accepting creates requirements r3 and a new quote version (QuoteService.accept_alternative).
"""
from __future__ import annotations

from pydantic import BaseModel

from quote_ai.domain.models import FulfilmentResult, FulfilmentStatus, LineFulfilment, Product, QuoteLine

NO_DATE = 10**6   # a backorder with no known receipt: never on time


class Stock(BaseModel):
    """One SKU's row of quote_ai_sales.inventory_snapshot (days are counted from the snapshot)."""
    sku_id: str
    available: int
    reserved: int = 0
    lead_time_days: int                    # from stock to the customer
    replenishment_qty: int = 0
    replenishment_day: int | None = None   # day the next receipt reaches the warehouse
    earliest_full_day: int | None = None   # beyond the next receipt: the day the whole quantity is in (AI-15)


def check_line(line: QuoteLine, sku_id: str, stock: Stock, delivery_days: int) -> LineFulfilment:
    free = max(stock.available - stock.reserved, 0)
    now = min(free, line.quantity)
    rest = line.quantity - now
    if rest == 0:
        ready = stock.lead_time_days
        return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity, status=FulfilmentStatus.FULL,
                              now=now, ready_day=ready, late=ready > delivery_days)
    if stock.replenishment_day is None or stock.replenishment_qty < rest:
        if stock.earliest_full_day is not None:
            ready = stock.earliest_full_day + stock.lead_time_days
            return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity,
                                  status=FulfilmentStatus.ALTERNATIVE_DATE, now=now, later=rest, later_day=ready,
                                  ready_day=ready, late=ready > delivery_days)
        return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity,
                              status=FulfilmentStatus.BACKORDER, now=now, later=rest, ready_day=NO_DATE, late=True)
    later_day = stock.replenishment_day + stock.lead_time_days
    status = FulfilmentStatus.PARTIAL if now else FulfilmentStatus.BACKORDER
    return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity, status=status, now=now,
                          later=rest, later_day=later_day, ready_day=later_day, late=later_day > delivery_days)


def check_product(product: Product, lines: list[QuoteLine], stock: dict[str, Stock], delivery_days: int,
                  inventory_snapshot: str) -> FulfilmentResult:
    out = []
    for line in lines:
        sku = product.sku_for(line.size)
        if sku is None or sku.sku_id not in stock:
            # no stock row for the SKU: nothing can be promised (never on time)
            sku_id = sku.sku_id if sku else f"{product.product_id}-{line.size}"
            out.append(LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity,
                                      status=FulfilmentStatus.BACKORDER, now=0, later=line.quantity,
                                      ready_day=NO_DATE, late=True))
            continue
        out.append(check_line(line, sku.sku_id, stock[sku.sku_id], delivery_days))
    return FulfilmentResult(product_id=product.product_id, lines=out, inventory_snapshot=inventory_snapshot)


def summary(r: FulfilmentResult) -> dict:
    """What the Shortlist screen's fulfilment column shows: the status, how much now and later, by when, the label."""
    later_day = max((l.later_day for l in r.lines if l.later_day is not None), default=None)
    ready = max(l.ready_day for l in r.lines) if r.lines else None
    return {"status": r.status.value, "now": sum(l.now for l in r.lines), "later": sum(l.later for l in r.lines),
            "later_day": later_day, "ready_day": None if ready == NO_DATE else ready, "on_time": r.on_time,
            "label": r.label, "inventory_snapshot": r.inventory_snapshot}
