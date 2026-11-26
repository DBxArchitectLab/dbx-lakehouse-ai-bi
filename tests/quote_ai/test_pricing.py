"""Pricing is a rule (AI-18): price_quote converts units, picks the effective-dated tier row, refuses below the margin
floor, and returns the price book row. QuoteService.price prices every line with it. The discount bands (AI-21)."""
from datetime import date

import pytest

import lakeside_fixtures as fx
from agent_fixtures import confirmed, priced
from quote_ai.rules import pricing
from quote_ai.rules.policy import Policy
from quote_ai.services import QuoteError

BOX = {"GLOVE-A-M": pricing.Packaging(selling_unit="box", gloves_per={"each": 1, "box": 100, "case": 1000})}


def book(*extra):
    return [pricing.PriceBookRow(id="PB-A-M-1", sku_id="GLOVE-A-M", tier="standard", min_units=1, list_price=8.60,
                                 unit_price=8.60, cost=6.50, min_margin=0.12),
            pricing.PriceBookRow(id="PB-A-M-250", sku_id="GLOVE-A-M", tier="standard", min_units=250,
                                 list_price=8.60, unit_price=8.30, cost=6.50, min_margin=0.12), *extra]


def test_400_boxes_are_400_boxes_not_400_gloves():
    p = pricing.price_quote("GLOVE-A-M", 140, "box", "standard", book(), BOX)
    assert (p["boxes"], p["unit_price"], p["total"], p["price_book_id"]) == (140, 8.60, 1204.0, "PB-A-M-1")
    assert pricing.price_quote("GLOVE-A-M", 400, "boxes", "standard", book(), BOX)["boxes"] == 400
    assert pricing.price_quote("GLOVE-A-M", 40000, "gloves", "standard", book(), BOX)["boxes"] == 400


def test_10_cases_of_10_boxes_are_100_boxes():
    assert pricing.price_quote("GLOVE-A-M", 10, "case", "standard", book(), BOX)["boxes"] == 100


def test_the_tier_breaks_at_250_boxes():
    assert pricing.price_quote("GLOVE-A-M", 249, "box", "standard", book(), BOX)["unit_price"] == 8.60
    at = pricing.price_quote("GLOVE-A-M", 250, "box", "standard", book(), BOX)
    assert (at["unit_price"], at["price_book_id"], at["total"]) == (8.30, "PB-A-M-250", 2075.0)


def test_below_the_margin_floor_raises():
    cheap = pricing.PriceBookRow(id="PB-X", sku_id="GLOVE-A-M", tier="standard", min_units=500, list_price=8.60,
                                 unit_price=7.00, cost=6.50, min_margin=0.12)       # floor 7.28
    with pytest.raises(pricing.PricingError, match="margin floor"):
        pricing.price_quote("GLOVE-A-M", 500, "box", "standard", book(cheap), BOX)


def test_a_unit_is_never_assumed():
    with pytest.raises(pricing.PricingError, match="no packaging"):
        pricing.price_quote("GLOVE-A-L", 10, "box", "standard", book(), BOX)
    with pytest.raises(pricing.PricingError, match="unknown unit"):
        pricing.price_quote("GLOVE-A-M", 10, "pallet", "standard", book(), BOX)
    with pytest.raises(pricing.PricingError, match="whole number"):
        pricing.price_quote("GLOVE-A-M", 150, "each", "standard", book(), BOX)


def test_rows_are_effective_dated():
    old = pricing.PriceBookRow(id="PB-OLD", sku_id="GLOVE-A-M", tier="standard", min_units=1, list_price=8.40,
                               unit_price=8.40, cost=6.50, min_margin=0.12, valid_to=date(2026, 10, 31))
    new = pricing.PriceBookRow(id="PB-NEW", sku_id="GLOVE-A-M", tier="standard", min_units=1, list_price=8.60,
                               unit_price=8.60, cost=6.50, min_margin=0.12, valid_from=date(2026, 11, 1))
    assert pricing.price_quote("GLOVE-A-M", 10, "box", "standard", [old, new], BOX,
                               on=date(2026, 10, 15))["price_book_id"] == "PB-OLD"
    assert pricing.price_quote("GLOVE-A-M", 10, "box", "standard", [old, new], BOX,
                               on=date(2026, 11, 14))["price_book_id"] == "PB-NEW"
    with pytest.raises(pricing.PricingError, match="no price book row"):
        pricing.price_quote("GLOVE-A-M", 10, "box", "contract", [old, new], BOX)


def test_quote_service_prices_every_line_with_price_quote():
    """From AI-18 PRICED means price_quote's number: the four Lakeside lines, Glove A at $8.60 from the price book."""
    svc, q = priced()
    assert q.price_method == "price_quote" and q.state.value == "PRICED"
    assert [(l.size, l.quantity, l.unit_price, l.price_book_id) for l in q.lines] == [
        ("S", 60, 8.60, "PB-A-S"), ("M", 140, 8.60, "PB-A-M"), ("L", 140, 8.60, "PB-A-L"), ("XL", 60, 8.60, "PB-A-XL")]
    assert q.total == 3440.0
    assert svc.repo.events(q.quote_id)[-1].detail["price_book_ids"] == ["PB-A-S", "PB-A-M", "PB-A-L", "PB-A-XL"]


def test_pricing_refuses_before_a_shortlist_and_says_why():
    svc, q = confirmed()
    with pytest.raises(QuoteError, match="needs a SKU"):
        svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book())


@pytest.mark.parametrize("asked, band", [(8.26, "NONE"), (8.00, "MANAGER"), (7.74, "MANAGER"), (7.22, "REFUSED"),
                                         (7.20, "REFUSED")])
def test_discount_bands(asked, band):
    """4%, 7% (Lakeside), 10%, 16% and below the margin floor: none, manager, manager, refused, refused."""
    row = fx.price_book()[0]
    b = pricing.discount_band(row.list_price, asked, row, Policy().pricing)
    assert b["approval"] == band and b["can_request_approval"] == (band == "MANAGER")
