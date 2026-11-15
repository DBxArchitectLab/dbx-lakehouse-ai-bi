"""Fulfilment feasibility, per quote line: can DBxLab ship it by the requested date?

A separate decision from eligibility. A product that misses a hard delivery date is not excluded; it is an
alternative that needs the customer's acceptance (plan 4.1).
"""
from __future__ import annotations

from pydantic import BaseModel

from quote_ai.domain.models import FulfilmentResult, FulfilmentStatus, LineFulfilment, Product, QuoteLine


class Stock(BaseModel):
    sku_id: str
    available: int
    reserved: int = 0
    lead_time_days: int                    # from stock to the customer
    replenishment_qty: int = 0
    replenishment_day: int | None = None   # day the replenishment reaches the warehouse


def check_line(line: QuoteLine, sku_id: str, stock: Stock, delivery_days: int) -> LineFulfilment:
    free = max(stock.available - stock.reserved, 0)
    now = min(free, line.quantity)
    rest = line.quantity - now
    if rest == 0:
        ready = stock.lead_time_days
        return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity, status=FulfilmentStatus.FULL,
                              now=now, ready_day=ready, late=ready > delivery_days)
    if stock.replenishment_day is None or stock.replenishment_qty < rest:
        return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity,
                              status=FulfilmentStatus.BACKORDER, now=now, later=rest, ready_day=10**6, late=True)
    later_day = stock.replenishment_day + stock.lead_time_days
    status = FulfilmentStatus.PARTIAL if now else FulfilmentStatus.BACKORDER
    return LineFulfilment(size=line.size, sku_id=sku_id, quantity=line.quantity, status=status, now=now,
                          later=rest, later_day=later_day, ready_day=later_day, late=later_day > delivery_days)


def check_product(product: Product, lines: list[QuoteLine], stock: dict[str, Stock], delivery_days: int,
                  inventory_snapshot: str) -> FulfilmentResult:
    out = []
    for line in lines:
        sku = product.sku_for(line.size)
        out.append(check_line(line, sku.sku_id, stock[sku.sku_id], delivery_days))
    return FulfilmentResult(product_id=product.product_id, lines=out, inventory_snapshot=inventory_snapshot)
