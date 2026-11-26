"""The product model (AI-03): a box is data, never an assumption. Unit conversion, mapping review, the Mapping suite."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quote_ai.mock import catalog as mock
from quote_ai.product.device_mapping import UnresolvedPackage, gloves_per_unit, unit_level, units_to_gloves
from quote_ai.product.mapping_review import (Confidence, Status, auto_match, match_catalog, offerable, reconcile,
                                             review_queue, to_domain_sku, verify)

ROOT = Path(__file__).resolve().parents[2]
SUITE = ROOT / "evaluation" / "quote_ai" / "v1" / "mapping" / "cases.jsonl"
AT = datetime(2026, 11, 20, tzinfo=timezone.utc)
CASE = [{"di": "CASE", "contains_di": "BOX", "pkg_quantity": 10},
        {"di": "BOX", "contains_di": "BASE", "pkg_quantity": 100}]


# ------------------------------------------------------------------ gloves per unit

def test_case_of_ten_boxes_of_100():
    levels = [{"di": "CASE", "contains_di": "BOX", "pkg_quantity": 10},
              {"di": "BOX", "contains_di": "BASE", "pkg_quantity": 100}]
    assert gloves_per_unit(levels, "CASE") == 1000


def test_each_box_and_case_resolve_to_their_glove_counts():
    assert [gloves_per_unit(CASE, di) for di in ("BASE", "BOX", "CASE")] == [1, 100, 1000]
    assert [unit_level(CASE, di) for di in ("BASE", "BOX", "CASE")] == ["each", "box", "case"]


def test_a_missing_level_raises_never_assume_100():
    with pytest.raises(UnresolvedPackage):
        gloves_per_unit([CASE[0]], "CASE", base_di="BASE")       # the box level is missing
    with pytest.raises(UnresolvedPackage):
        gloves_per_unit([{"di": "BOX", "contains_di": "BASE", "pkg_quantity": None}], "BOX", base_di="BASE")


def test_a_loop_in_the_package_levels_raises():
    loop = [{"di": "A", "contains_di": "B", "pkg_quantity": 2}, {"di": "B", "contains_di": "A", "pkg_quantity": 2}]
    with pytest.raises(UnresolvedPackage):
        gloves_per_unit(loop, "A", base_di="BASE")


def test_400_boxes_of_100_are_40000_gloves():
    assert units_to_gloves(400, gloves_per_unit(CASE, "BOX")) == 40_000


# ------------------------------------------------------------------ review

def test_no_sku_from_package_quantity_alone():
    # a GUDID case with no DBxLab SKU is never offered as "a box"
    m = auto_match(sku=None, package_di="CASE")
    assert m.verification_status == "unverified"
    assert not offerable(m)
    with pytest.raises(ValueError, match="no DBxLab SKU"):
        verify(m, "rep.reviewer", AT)


def test_every_match_starts_unverified_and_only_a_person_makes_it_offerable():
    sku = {"sku_id": "GLOVE-A-M", "product_id": "GLOVE-A", "unit": "box", "gloves_per_unit": 100,
           "supplier_catalog_number": "NX-PF-M"}
    m = auto_match(sku, "BOX", "nx-pf-m", CASE + [{"di": "BASE"}], primary_di="BASE")
    assert m.mapping_confidence == Confidence.HIGH and m.verification_status == Status.UNVERIFIED
    assert not offerable(m) and review_queue([m]) == [m]
    v = verify(m, "maria.ops", AT)
    assert offerable(v) and v.verified_by == "maria.ops" and v.mapping_method.startswith("reviewed:")
    assert review_queue([v]) == []


def test_a_changed_gudid_package_supersedes_a_verified_mapping():
    sku = {"sku_id": "GLOVE-A-M", "product_id": "GLOVE-A", "unit": "box", "gloves_per_unit": 100,
           "supplier_catalog_number": "NX"}
    verified = verify(auto_match(sku, "BOX", "NX", CASE, primary_di="BASE"), "maria.ops", AT)
    same = reconcile([auto_match(sku, "BOX", "NX", CASE, primary_di="BASE")], [verified])
    assert same == [verified]
    changed = [CASE[0], {"di": "BOX", "contains_di": "BASE", "pkg_quantity": 50}]   # GUDID now says 50 a box
    out = reconcile([auto_match(sku, "BOX", "NX", changed, primary_di="BASE")], [verified])
    assert out[0].verification_status == Status.SUPERSEDED and out[0].gloves_per_unit == 50
    assert not offerable(out[0]) and review_queue(out) == out


def test_the_domain_sku_is_mapping_verified_only_with_a_verified_exact_count():
    sku = mock.skus()[1]                                        # GLOVE-A-M, a box of 100
    m = auto_match(sku | {"supplier_catalog_number": "NX"}, "BOX", "NX", CASE, primary_di="BASE")
    assert not to_domain_sku(sku, [m]).mapping_verified
    assert to_domain_sku(sku, [verify(m, "maria.ops", AT)]).mapping_verified
    case = verify(auto_match(sku | {"supplier_catalog_number": "NX"}, "CASE", "NX", CASE, primary_di="BASE"),
                  "maria.ops", AT)                              # someone verified the case: still not a box of 100
    assert not to_domain_sku(sku, [case]).mapping_verified


def test_a_matching_run_binds_skus_and_proposes_every_package_level():
    devices = [{"primary_di": "BASE", "catalog_number": "NX-PF-M"}]
    packages = [{"primary_di": "BASE", "package_di": "BOX", "contains_di": "BASE", "pkg_quantity": 100},
                {"primary_di": "BASE", "package_di": "CASE", "contains_di": "BOX", "pkg_quantity": 10}]
    skus = mock.bind_devices(mock.skus()[:1], [{"primary_di": "BASE", "catalog_number": "NX-PF-M",
                                                "box_quantity": 100}])
    assert skus[0]["supplier_di"] == "BASE"
    out = {m.unit_level: m for m in match_catalog(skus, devices, packages)}
    assert out["box"].mapping_confidence == Confidence.HIGH
    assert out["case"].mapping_confidence == Confidence.MEDIUM and out["case"].gloves_per_unit == 1000
    assert out["each"].mapping_confidence == Confidence.MEDIUM
    assert not any(offerable(m) for m in out.values())


# ------------------------------------------------------------------ the Mapping suite

def _suite():
    return [json.loads(line) for line in SUITE.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("case", _suite(), ids=lambda c: c["id"])
def test_mapping_suite(case):
    exp = case["expected"]
    m = auto_match(case["sku"], case["package_di"], case["catalog_number"], case["levels"], case["primary_di"])
    assert m.unit_level == exp["unit_level"]
    assert m.gloves_per_unit == exp["gloves_per_unit"]
    assert (None if m.gloves_per_unit is None else units_to_gloves(case["quantity"], m.gloves_per_unit)) == \
        exp["gloves"]
    assert m.mapping_confidence.value == exp["mapping_confidence"]
    assert m.verification_status == Status.UNVERIFIED and not offerable(m)   # nothing offered before a person
    if exp["verifiable"]:
        assert offerable(verify(m, "suite", AT))
    else:
        with pytest.raises(ValueError):
            verify(m, "suite", AT)


def test_the_suite_has_the_each_box_and_case_fixtures():
    units = {c["expected"]["unit_level"] for c in _suite()}
    assert {"each", "box", "case"} <= units and len(_suite()) >= 6


# ------------------------------------------------------------------ the synthetic catalog

def test_the_catalog_has_the_five_tracer_gloves_in_four_sizes_and_a_box_of_200():
    skus = mock.skus()
    assert {s["product_id"] for s in skus} == {f"GLOVE-{c}" for c in "ABCDEF"}
    assert next(s for s in skus if s["sku_id"] == "GLOVE-A-M")["catalog_number"] == "DBX-NE-M-100"
    assert next(s for s in skus if s["sku_id"] == "GLOVE-F-M")["catalog_number"] == "DBX-NE-M-200"
    assert all(s["demo_data"] for s in skus) and len(skus) == 24


def test_quote_history_is_deterministic_and_carries_the_planted_signal():
    h = mock.quote_history()
    assert h == mock.quote_history() and all(r["decided_at"] > r["quoted_at"] for r in h)
    cheap = [r["outcome"] == "won" for r in h if r["unit_price"] < r["customer_current_price"] * 0.97]
    dear = [r["outcome"] == "won" for r in h if r["unit_price"] > r["customer_current_price"] * 1.05]
    assert sum(cheap) / len(cheap) > sum(dear) / len(dear) + 0.2


def test_bind_devices_never_binds_a_box_of_the_wrong_size():
    out = mock.bind_devices(mock.skus(), [{"primary_di": "D1", "catalog_number": "X", "box_quantity": 200}])
    bound = [s for s in out if s["supplier_di"]]
    assert [s["sku_id"] for s in bound] == ["GLOVE-F-S"]
