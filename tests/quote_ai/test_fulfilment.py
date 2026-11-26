"""Fulfilment (AI-15), the Fulfilment suite's fixtures: full, partial, backorder, alternative date, and a late partial
that is an alternative needing acceptance, never on time."""
import json
from pathlib import Path

import pytest

from quote_ai.domain.models import FulfilmentStatus as F, QuoteLine
from quote_ai.rules import fulfilment, uc
from quote_ai.rules.fulfilment import Stock
import lakeside_fixtures as fx

ROOT = Path(__file__).resolve().parents[2]
CASES = [json.loads(l) for l in (ROOT / "evaluation/quote_ai/v1/fulfilment/cases.jsonl").read_text(
    encoding="utf-8").splitlines() if l.strip()]


def line(qty=400):
    return QuoteLine(line_no=1, size="M", quantity=qty, unit="box")


def test_full():
    r = fulfilment.check_line(line(), "X", Stock(sku_id="X", available=1000, lead_time_days=7), 10)
    assert (r.status, r.now, r.late, r.ready_day) == (F.FULL, 400, False, 7)


def test_partial_250_now_150_in_three_weeks_is_late():
    r = fulfilment.check_line(line(), "X", Stock(sku_id="X", available=250, lead_time_days=7, replenishment_qty=500,
                                                 replenishment_day=14), 10)
    assert (r.status, r.now, r.later, r.later_day, r.late) == (F.PARTIAL, 250, 150, 21, True)


def test_backorder():
    r = fulfilment.check_line(line(), "X", Stock(sku_id="X", available=0, lead_time_days=2, replenishment_qty=500,
                                                 replenishment_day=5), 10)
    assert (r.status, r.now, r.later, r.late) == (F.BACKORDER, 0, 400, False)


def test_alternative_date_when_the_next_receipt_is_not_enough():
    r = fulfilment.check_line(line(), "X", Stock(sku_id="X", available=100, lead_time_days=7, replenishment_qty=50,
                                                 replenishment_day=5, earliest_full_day=30), 10)
    assert (r.status, r.ready_day, r.late) == (F.ALTERNATIVE_DATE, 37, True)


def test_reserved_stock_is_not_free():
    r = fulfilment.check_line(line(), "X", Stock(sku_id="X", available=500, reserved=200, lead_time_days=7,
                                                 replenishment_qty=500, replenishment_day=14), 10)
    assert (r.status, r.now) == (F.PARTIAL, 300)


def test_glove_c_for_lakeside_is_an_alternative_that_needs_acceptance():
    lines = [QuoteLine(line_no=i + 1, size=s, quantity=fx.LAKESIDE_SPLIT[s], unit="box")
             for i, s in enumerate(fx.SIZES)]
    c = fulfilment.check_product(fx.product("C"), lines, fx.stock(), 10, "inv")
    a = fulfilment.check_product(fx.product("A"), lines, fx.stock(), 10, "inv")
    assert (c.on_time, c.label, c.status) == (False, "alternative, needs acceptance", F.PARTIAL)
    assert (a.on_time, a.label, a.status) == (True, "on time", F.FULL)
    s = fulfilment.summary(c)
    assert (s["now"], s["later"], s["later_day"]) == (250, 150, 21)


def test_no_stock_row_is_never_on_time():
    r = fulfilment.check_product(fx.product("A"), [line()], {}, 10, "inv")
    assert not r.on_time and r.status == F.BACKORDER


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_fulfilment_suite_case(case):
    r = fulfilment.check_line(line(case["quantity"]), "X", Stock(sku_id="X", **case["stock"]), case["delivery_days"])
    assert r.status.value == case["expected"]["status"]
    assert r.late == case["expected"]["late"]          # a line past the hard date is never on time


def test_the_uc_function_side():
    lines = [{"line_no": 1, "size": "M", "quantity": 140, "unit": "box"}]
    skus = [s.model_dump() for s in fx.product("C").skus]
    stock = [s.model_dump() for s in fx.stock().values()]
    out = json.loads(uc.fulfilment_json(json.dumps(skus), json.dumps(lines), json.dumps(stock), 10, "inv"))
    assert out["summary"]["label"] == "alternative, needs acceptance" and out["lines"][0]["now"] == 65
    assert uc.fulfilment_result(json.dumps(out)).on_time is False
