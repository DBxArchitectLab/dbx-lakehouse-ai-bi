"""Pricing is a rule, not a prompt.

price_quote (AI-18): the price of one quote line from the price book. The quantity is converted to the SKU's selling
            unit from its packaging (never assumed), the effective-dated row for the account tier and volume is looked
            up, the margin floor is checked (below it: PricingError, never a quietly lower price), and the price book
            row ID is returned, so every quote shows where its price came from. QuoteService.price calls it for every
            line: from AI-18, PRICED means price_quote's number (v1's list price, AI-07, is gone from that path). The
            agent's price_quote tool and the Unity Catalog function quote_ai_ai.price_quote call the same function.
approval_required (AI-21): whether a discount needs a manager, from the policy's bands (config/quote_ai/policy.yml).
AI-24: pricing runs as the signed-in rep, and a rep never sees DBxLab's cost (the price book masks it). The margin
floor comes as floor_price (cost * (1 + min_margin), computed in the price book view), so the rule still holds with
cost masked; the margin itself is shown only to whoever can see the cost (a manager). Inputs are SKUs, quantities, the
customer tier and an asked price: never request text, so there is no parameter an injected email can write into.
"""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel

from quote_ai.rules.policy import PricingPolicy


class PricingError(ValueError):
    """A rule's answer (unknown unit, no row, below the floor): never retried, never talked around."""


class PriceBookRow(BaseModel):
    id: str
    sku_id: str
    tier: str
    min_units: int          # volume break, in the SKU's selling unit
    list_price: float
    unit_price: float       # the tier's price
    cost: float | None = None        # DBxLab's cost: None when masked (anyone but a manager, AI-24)
    min_margin: float       # e.g. 0.12: price must be at least cost * 1.12
    floor_price: float | None = None  # AI-24: cost * (1 + min_margin), from the price book view: visible to reps
    valid_from: date | None = None   # effective dating (AI-18): the row applies from this day ...
    valid_to: date | None = None     # ... up to and including this day; None = open


class Packaging(BaseModel):
    """How one SKU is packed: its selling unit and the gloves in each unit it can be ordered in."""
    selling_unit: str = "box"
    gloves_per: dict[str, int]        # {"each": 1, "box": 100, "case": 1000}


# Plurals and synonyms a request or a model may use; anything else is an unknown unit (an error, not a guess)
UNIT_ALIASES = {"boxes": "box", "bx": "box", "cases": "case", "cs": "case", "gloves": "each", "glove": "each",
                "ea": "each", "pieces": "each", "pcs": "each"}


def normalize_unit(unit: str) -> str:
    u = (unit or "").strip().lower()
    return UNIT_ALIASES.get(u, u)


def to_selling_unit(quantity: int, unit: str, gloves_per: dict[str, int], selling_unit: str) -> int:
    """Convert a quantity to the SKU's selling unit. Never assume a unit: an unknown unit is an error."""
    if unit not in gloves_per or selling_unit not in gloves_per:
        raise PricingError(f"unknown unit: {unit!r} or {selling_unit!r}")
    gloves = quantity * gloves_per[unit]
    if gloves % gloves_per[selling_unit]:
        raise PricingError(f"{quantity} {unit} is not a whole number of {selling_unit}")
    return gloves // gloves_per[selling_unit]


def _effective(r: PriceBookRow, on: date | None) -> bool:
    if on is None:
        return True
    return (r.valid_from is None or r.valid_from <= on) and (r.valid_to is None or on <= r.valid_to)


def book_row(rows: list[PriceBookRow], sku_id: str, tier: str, units: int, on: date | None = None) -> PriceBookRow:
    """The row for this SKU, tier and volume on a day: the highest volume break at or below the units."""
    candidates = [r for r in rows if r.sku_id == sku_id and r.tier == tier and r.min_units <= units
                  and _effective(r, on)]
    if not candidates:
        raise PricingError(f"no price book row for {sku_id}, tier {tier}, {units} units"
                           + (f" on {on.isoformat()}" if on else ""))
    return max(candidates, key=lambda r: r.min_units)


def floor(row: PriceBookRow) -> float:
    """The lowest price the margin allows: the view's floor_price, else from the cost (a manager's, or the tests')."""
    if row.floor_price is not None:
        return row.floor_price
    if row.cost is None:
        raise PricingError(f"{row.id}: no floor price and no cost: the margin floor can't be checked")
    return row.cost * (1 + row.min_margin)


def book_price(sku_id: str, units: int, tier: str, rows: list[PriceBookRow], on: date | None = None) -> PriceBookRow:
    """The price book row for units already in the selling unit, checked against the margin floor."""
    row = book_row(rows, sku_id, tier, units, on)
    if row.unit_price < floor(row) - 1e-9:
        raise PricingError(f"{sku_id}: book price below the margin floor")
    return row


def price_quote(sku_id: str, qty: int, unit: str, account_tier: str, book: list[PriceBookRow],
                packaging: dict[str, Packaging], on: date | None = None) -> dict:
    """One quote line, priced by rule. 400 boxes is 400 boxes, never 400 gloves: the conversion uses the SKU's
    packaging, and a SKU without packaging data is an error. Raises PricingError for an unknown unit, a quantity that
    is not a whole number of selling units, no price book row, or a book price below the margin floor."""
    pack = packaging.get(sku_id)
    if pack is None:
        raise PricingError(f"{sku_id}: no packaging data, so the unit can't be converted (never assumed)")
    units = to_selling_unit(qty, normalize_unit(unit), pack.gloves_per, pack.selling_unit)
    row = book_price(sku_id, units, account_tier, book, on)
    return {"sku_id": sku_id, "boxes": units, "selling_unit": pack.selling_unit, "unit_price": row.unit_price,
            "list_price": row.list_price, "total": round(units * row.unit_price, 2), "price_book_id": row.id,
            "tier": account_tier}


def below_margin(unit_price: float, row: PriceBookRow) -> bool:
    return unit_price < floor(row) - 1e-9


def approval_required(list_price: float, unit_price: float, row: PriceBookRow, policy: PricingPolicy) -> str:
    """NONE, MANAGER or REFUSED (AI-21). The margin floor first: below it, nobody can approve. Then the discount
    against the list price price_quote returned: up to the rep's limit (5%) no approval, up to the manager's limit
    (15%) a manager approves that quote version, above it refused. No model and no prompt decide this."""
    if below_margin(unit_price, row):
        return "REFUSED"
    discount = round(1 - unit_price / list_price, 6)
    if discount <= policy.rep_discount_limit:
        return "NONE"
    if discount <= policy.manager_discount_limit:
        return "MANAGER"
    return "REFUSED"


def discount_band(list_price: float, unit_price: float, row: PriceBookRow, policy: PricingPolicy) -> dict:
    """What the Pricing screen and the agent show about an asked price: the discount, the band, the margin, and
    whether an approval can be requested at all (not for REFUSED: there is no approval button)."""
    need = approval_required(list_price, unit_price, row, policy)
    return {"list_price": list_price, "unit_price": unit_price,
            "discount_pct": round(100 * (1 - unit_price / list_price), 2),
            "margin_pct": round(100 * (unit_price / row.cost - 1), 2) if row.cost else None,   # masked: None
            "floor": round(floor(row), 2), "approval": need,
            "can_request_approval": need == "MANAGER",
            "rep_limit_pct": round(100 * policy.rep_discount_limit, 2),
            "manager_limit_pct": round(100 * policy.manager_discount_limit, 2)}
