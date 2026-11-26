"""The rules, case by case: the requirement contract (plan 4.6), fulfilment, pricing, freshness, the state machine."""
from datetime import timedelta

import pytest

from quote_ai.domain.models import Check, Eligibility, QuoteLine, QuoteState as S
from quote_ai.rules import eligibility, fulfilment, pricing
from quote_ai.rules.freshness import stale_inputs
from quote_ai.rules.policy import Policy, load_policy
from quote_ai.rules.quote_states import InvalidTransition, check_transition
import lakeside_fixtures as fx

POLICY = Policy()


def lines():
    return [QuoteLine(line_no=i + 1, size=s, quantity=fx.LAKESIDE_SPLIT[s], unit="box")
            for i, s in enumerate(fx.SIZES)]


def confirmed():
    return fx.lakeside_r1().model_copy(update={"version": 2, "quantity_by_size": fx.LAKESIDE_SPLIT,
                                               "gloves_per_unit": 100, "missing": []})


# ------------------------------------------------------------ eligibility: the requirement contract

@pytest.mark.parametrize("letter, snapshot, expected", [
    ("A", "ev-41", Eligibility.ELIGIBLE),
    ("B", "ev-41", Eligibility.NEEDS_EVIDENCE),   # its chemo claim is only extracted text
    ("B", "ev-57", Eligibility.ELIGIBLE),         # same glove, its report verified: both cases stay in the suite
    ("C", "ev-41", Eligibility.ELIGIBLE),         # suitable; whether it ships in time is fulfilment's question
    ("D", "ev-41", Eligibility.ELIGIBLE),
    ("E", "ev-41", Eligibility.EXCLUDED),         # natural rubber latex
])
def test_requirement_contract(letter, snapshot, expected):
    r = eligibility.check_product(fx.product(letter), confirmed(), fx.evidence(snapshot), snapshot)
    assert r.status == expected
    assert r.evidence_snapshot == snapshot


def test_every_stated_requirement_has_a_check():
    r = eligibility.check_product(fx.product("A"), confirmed(), fx.evidence(), "ev-41")
    assert {c.requirement for c in r.checks} == {"material", "powder_free", "intended_use", "latex_free",
                                                 "chemo_tested", "sizes", "in_distribution"}
    assert all(c.result == Check.MATCH for c in r.checks)


def test_an_extracted_claim_never_decides():
    r = eligibility.check_product(fx.product("B"), confirmed(), fx.evidence("ev-41"), "ev-41")
    chemo = next(c for c in r.checks if c.requirement == "chemo_tested")
    assert chemo.result == Check.UNKNOWN and "DESC-B" not in chemo.evidence


def test_an_unverified_mapping_is_unknown_not_a_match():
    r = eligibility.check_product(fx.product("A", mapping_verified=False), confirmed(), fx.evidence(), "ev-41")
    assert r.status == Eligibility.NEEDS_EVIDENCE


# ------------------------------------------------------------ fulfilment

def test_glove_a_ships_in_full_by_day_7():
    r = fulfilment.check_product(fx.product("A"), lines(), fx.stock(), 10, "inv-1")
    assert r.on_time and {l.ready_day for l in r.lines} == {7}


def test_glove_c_is_partial_and_late():
    r = fulfilment.check_product(fx.product("C"), lines(), fx.stock(), 10, "inv-1")
    assert not r.on_time
    assert sum(l.now for l in r.lines) == 250 and sum(l.later for l in r.lines) == 150
    assert max(l.ready_day for l in r.lines) == 21


# ------------------------------------------------------------ pricing

@pytest.mark.parametrize("qty, unit, expected_boxes", [(400, "box", 400), (10, "case", 100), (5000, "each", 50)])
def test_units_are_converted_never_assumed(qty, unit, expected_boxes):
    per = {"each": 1, "box": 100, "case": 1000}
    assert pricing.to_selling_unit(qty, unit, per, "box") == expected_boxes


def test_unknown_unit_is_an_error():
    with pytest.raises(pricing.PricingError):
        pricing.to_selling_unit(1, "pallet", {"box": 100}, "box")


@pytest.mark.parametrize("asked, expected", [(8.30, "NONE"), (8.00, "MANAGER"), (7.50, "MANAGER"),
                                             (7.20, "REFUSED")])
def test_discount_bands(asked, expected):
    row = fx.price_book()[0]        # $8.60 list, $6.50 cost, 12% margin floor = $7.28
    assert pricing.approval_required(row.list_price, asked, row, POLICY.pricing) == expected


def test_lakeside_7_percent_needs_a_manager():
    row = pricing.book_price("GLOVE-A-M", 140, "standard", fx.price_book())
    assert row.list_price == 8.60
    assert pricing.approval_required(row.list_price, 8.00, row, POLICY.pricing) == "MANAGER"


# ------------------------------------------------------------ freshness, states, policy

def test_stale_inventory_is_named():
    inputs = fx.fresh_inputs() | {"inventory": fx.NOW - timedelta(hours=30)}
    assert stale_inputs(inputs, fx.NOW, POLICY.freshness) == ["inventory"]


def test_priced_cannot_jump_to_issued():
    with pytest.raises(InvalidTransition):
        check_transition(S.PRICED, S.ISSUED)


def test_policy_file_loads():
    from pathlib import Path
    p = load_policy(Path(__file__).resolve().parents[2] / "config" / "quote_ai" / "policy.yml")
    assert p.pricing.rep_discount_limit == 0.05 and p.freshness.max_age_hours["inventory"] == 24
