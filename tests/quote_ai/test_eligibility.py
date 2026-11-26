"""The eligibility engine (AI-15): three states, one check per stated requirement, only business truth decides.
The Eligibility suite's zero-tolerance assertions, on the tracer gloves and on the demo evidence the app reads."""
import json

import pytest

from quote_ai.domain.models import Check, Eligibility, Evidence, Requirements, SourceType as ST, Verification as V
from quote_ai.product.evidence import rows_to_domain
from quote_ai.rules import eligibility, uc
from quote_ai.storage.evidence_store import DemoEvidenceStore
from quote_ai.storage.product_catalog import DemoProductCatalog
import lakeside_fixtures as fx

ALL = [f"GLOVE-{l}" for l in "ABCDEFGHIJKLMNOPQR"]


def r2(**update) -> Requirements:
    return fx.lakeside_r1().model_copy(update={"version": 2, "quantity_by_size": fx.LAKESIDE_SPLIT,
                                               "gloves_per_unit": 100, "missing": []} | update)


def ev(pid, attribute, value, source=ST.GUDID, verification=V.VERIFIED, sid=None):
    return Evidence(product_id=pid, attribute=attribute, value=value, source_type=source,
                    source_id=sid or f"{source.value}:{attribute}", verification=verification)


def check(result, requirement):
    return next(c for c in result.checks if c.requirement == requirement)


def test_latex_labelled_contains_nrl_is_a_verified_mismatch_and_excluded():
    r = eligibility.check_product(fx.product("E"), r2(), fx.evidence(), "ev-41")
    c = check(r, "latex_free")
    assert c.result == Check.MISMATCH and r.status == Eligibility.EXCLUDED
    assert c.reason == "GUDID: labeled contains natural rubber latex" and c.evidence


def test_missing_latex_evidence_is_unknown_and_needs_evidence():
    rows = [e for e in fx.evidence() if not (e.product_id == "GLOVE-A" and e.attribute == "latex_free")]
    r = eligibility.check_product(fx.product("A"), r2(), rows, "ev-41")
    assert check(r, "latex_free").result == Check.UNKNOWN and r.status == Eligibility.NEEDS_EVIDENCE


def test_a_chemo_claim_only_from_ai_extraction_is_unknown_and_shown_as_a_claim():
    r = eligibility.check_product(fx.product("B"), r2(), fx.evidence("ev-41"), "ev-41")
    c = check(r, "chemo_tested")
    assert c.result == Check.UNKNOWN and c.evidence == []
    assert c.claims == ["DESC-B (ai_extraction, unverified)"]


def test_all_hard_checks_verified_match_is_eligible_with_the_evidence_used():
    r = eligibility.check_product(fx.product("A"), r2(), fx.evidence(), "ev-41")
    assert r.status == Eligibility.ELIGIBLE and all(c.result == Check.MATCH and c.evidence for c in r.checks)
    assert check(r, "latex_free").reason == "GUDID: labeled no natural rubber latex"


@pytest.mark.parametrize("update", [{"target_unit_price": 1.00}, {"target_unit_price": None}, {"sterile": None}])
def test_a_soft_requirement_never_changes_eligibility(update):
    base = eligibility.check_product(fx.product("A"), r2(), fx.evidence(), "ev-41")
    other = eligibility.check_product(fx.product("A"), r2(**update), fx.evidence(), "ev-41")
    assert other.status == base.status and [c.requirement for c in other.checks] == [c.requirement for c in base.checks]


def test_glove_a_eligible_glove_b_needs_evidence():
    a = eligibility.check_product(fx.product("A"), r2(), fx.evidence(), "ev-41")
    b = eligibility.check_product(fx.product("B"), r2(), fx.evidence(), "ev-41")
    assert (a.status, b.status) == (Eligibility.ELIGIBLE, Eligibility.NEEDS_EVIDENCE)


def test_every_stated_requirement_has_a_check_powder_free_and_exam_use_included():
    req = r2()
    assert eligibility.stated(req) == ["material", "powder_free", "intended_use", "latex_free", "chemo_tested",
                                       "sizes", "in_distribution"]
    r = eligibility.check_product(fx.product("A"), req, fx.evidence(), "ev-41")
    assert [c.requirement for c in r.checks] == eligibility.stated(req)


def test_a_glove_that_is_not_powder_free_is_excluded():
    rows = [e for e in fx.evidence() if not (e.product_id == "GLOVE-A" and e.attribute == "powder_free")]
    rows.append(ev("GLOVE-A", "powder_free", False, ST.SUPPLIER_DOCUMENT, sid="SPEC-A"))
    r = eligibility.check_product(fx.product("A"), r2(), rows, "ev-41")
    assert check(r, "powder_free").result == Check.MISMATCH and r.status == Eligibility.EXCLUDED


def test_a_surgical_glove_is_excluded_from_an_exam_request():
    rows = [e for e in fx.evidence() if not (e.product_id == "GLOVE-A" and e.attribute == "intended_use")]
    rows.append(ev("GLOVE-A", "intended_use", "surgical"))
    r = eligibility.check_product(fx.product("A"), r2(), rows, "ev-41")
    assert check(r, "intended_use").result == Check.MISMATCH


@pytest.mark.parametrize("verification", [V.UNVERIFIED, V.CONFLICTING, V.SUPERSEDED])
def test_no_check_matches_on_a_value_that_is_not_verified(verification):
    rows = [e for e in fx.evidence() if not (e.product_id == "GLOVE-A" and e.attribute == "latex_free")]
    rows.append(ev("GLOVE-A", "latex_free", True, ST.SUPPLIER_DOCUMENT, verification))
    r = eligibility.check_product(fx.product("A"), r2(), rows, "ev-41")
    assert check(r, "latex_free").result == Check.UNKNOWN and r.status != Eligibility.ELIGIBLE


def test_a_box_of_200_is_not_the_confirmed_box_of_100():
    p = fx.product("A")
    for s in p.skus:
        s.gloves_per_unit = 200
    r = eligibility.check_product(p, r2(), fx.evidence(), "ev-41")
    assert check(r, "sizes").result == Check.MISMATCH and "200" in check(r, "sizes").reason


def test_not_in_distribution_is_excluded():
    rows = [e for e in fx.evidence() if not (e.product_id == "GLOVE-A" and e.attribute == "in_commercial_distribution")]
    rows.append(ev("GLOVE-A", "in_commercial_distribution", False))
    r = eligibility.check_product(fx.product("A"), r2(), rows, "ev-41")
    assert check(r, "in_distribution").result == Check.MISMATCH


# ------------------------------------------------------------ the demo evidence the app reads (A to F, G to R)

def demo(snapshot_scenarios=("glove_a_report",), signoffs=None):
    store = DemoEvidenceStore(scenarios=snapshot_scenarios, signoffs=signoffs)
    evidence = rows_to_domain(store.for_products(ALL))
    return {p.product_id: eligibility.check_product(p, r2(), evidence, "demo")
            for p in DemoProductCatalog().products(ALL)}


def test_lakeside_on_the_demo_evidence_three_eligible_b_needs_evidence_e_latex():
    out = demo()
    assert {p for p, r in out.items() if r.status == Eligibility.ELIGIBLE} == {"GLOVE-A", "GLOVE-C", "GLOVE-D"}
    assert {p for p, r in out.items() if r.status == Eligibility.NEEDS_EVIDENCE} == {"GLOVE-B", "GLOVE-O", "GLOVE-Q"}
    assert check(out["GLOVE-E"], "latex_free").result == Check.MISMATCH
    assert check(out["GLOVE-F"], "sizes").result == Check.MISMATCH            # a box of 200
    assert check(out["GLOVE-G"], "sizes").reason == "no sellable SKU in L"
    assert check(out["GLOVE-M"], "in_distribution").result == Check.MISMATCH  # discontinued
    b = check(out["GLOVE-B"], "chemo_tested")
    assert b.result == Check.UNKNOWN and any("ai_extraction" in c for c in b.claims)


def test_glove_b_eligible_once_its_report_is_verified():
    out = demo(("glove_a_report", "glove_b_report"), {("RPT-CHEMO-A", 1): "qa.demo", ("RPT-CHEMO-B", 1): "qa.demo"})
    assert out["GLOVE-B"].status == Eligibility.ELIGIBLE


def test_unknown_is_never_eligible_anywhere():
    for r in demo().values():
        if any(c.result == Check.UNKNOWN for c in r.checks):
            assert r.status != Eligibility.ELIGIBLE


# ------------------------------------------------------------ the Unity Catalog function's Python side

def test_the_uc_function_returns_the_same_answer_as_the_engine():
    cat = DemoProductCatalog()
    store = DemoEvidenceStore()
    for pid in ("GLOVE-A", "GLOVE-B", "GLOVE-E"):
        p = cat.products([pid])[0]
        skus = [s.model_dump() | {"product_name": p.name} for s in p.skus]
        out = uc.eligibility_result(uc.eligibility_json(r2().model_dump_json(), json.dumps(skus),
                                                        json.dumps(store.for_products([pid]), default=str), "snap"))
        direct = eligibility.check_product(p, r2(), rows_to_domain(store.for_products([pid])), "snap")
        assert out == direct


def test_the_uc_function_refuses_a_sku_it_cannot_find():
    with pytest.raises(ValueError):
        uc.eligibility_json(r2().model_dump_json(), "[]", "[]", "snap")
