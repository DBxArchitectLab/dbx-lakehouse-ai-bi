"""Pricing is a rule, not a prompt.

price_quote (AI-18): the price per quote line from the price book: units converted, tier by volume, margin floor.
approval_required (AI-21): whether a discount needs a manager, from the policy's bands.
"""
from __future__ import annotations

from pydantic import BaseModel

from quote_ai.rules.policy import PricingPolicy


class PricingError(ValueError):
    pass


class PriceBookRow(BaseModel):
    id: str
    sku_id: str
    tier: str
    min_units: int          # volume break, in the SKU's selling unit
    list_price: float
    unit_price: float       # the tier's price
    cost: float
    min_margin: float       # e.g. 0.12: price must be at least cost * 1.12


def to_selling_unit(quantity: int, unit: str, gloves_per: dict[str, int], selling_unit: str) -> int:
    """Convert a quantity to the SKU's selling unit. Never assume a unit: an unknown unit is an error."""
    if unit not in gloves_per or selling_unit not in gloves_per:
        raise PricingError(f"unknown unit: {unit!r} or {selling_unit!r}")
    gloves = quantity * gloves_per[unit]
    if gloves % gloves_per[selling_unit]:
        raise PricingError(f"{quantity} {unit} is not a whole number of {selling_unit}")
    return gloves // gloves_per[selling_unit]


def book_row(rows: list[PriceBookRow], sku_id: str, tier: str, units: int) -> PriceBookRow:
    candidates = [r for r in rows if r.sku_id == sku_id and r.tier == tier and r.min_units <= units]
    if not candidates:
        raise PricingError(f"no price book row for {sku_id}, tier {tier}, {units} units")
    return max(candidates, key=lambda r: r.min_units)


def price_quote(sku_id: str, units: int, tier: str, rows: list[PriceBookRow]) -> PriceBookRow:
    row = book_row(rows, sku_id, tier, units)
    if row.unit_price < row.cost * (1 + row.min_margin):
        raise PricingError(f"{sku_id}: book price below the margin floor")
    return row


def below_margin(unit_price: float, row: PriceBookRow) -> bool:
    return unit_price < row.cost * (1 + row.min_margin)


def approval_required(list_price: float, unit_price: float, row: PriceBookRow, policy: PricingPolicy) -> str:
    """NONE, MANAGER or REFUSED. The margin floor first: below it, nobody can approve."""
    if below_margin(unit_price, row):
        return "REFUSED"
    discount = round(1 - unit_price / list_price, 6)
    if discount <= policy.rep_discount_limit:
        return "NONE"
    if discount <= policy.manager_discount_limit:
        return "MANAGER"
    return "REFUSED"
