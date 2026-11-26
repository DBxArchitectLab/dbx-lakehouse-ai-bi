"""Supplier documents to evidence (AI-09): versions, units, ranges, and the five checks, one test per check. The
synthetic PDFs and their truth file agree (the generator self-test). No Databricks connection."""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import lakeside_fixtures as fx
from quote_ai.domain.models import Eligibility, Verification
from quote_ai.mock import documents as md
from quote_ai.product import evidence as ev
from quote_ai.rules.eligibility import check_product

ROOT = Path(__file__).resolve().parents[2]
AT = datetime(2026, 11, 20, tzinfo=timezone.utc)
A_SIGNED = {("RPT-CHEMO-A", 1): "qa.jane"}


def chemo(rows, sku="GLOVE-A-M", doc=None):
    return [r for r in rows if r.attribute_name == "chemo_tested" and r.sku_id == sku
            and (doc is None or r.document_id == doc)]


def glove_a_report(**overrides) -> ev.Document:
    d = ev.Document(document_id="RPT-CHEMO-A", version=1, document_type="test_report", supplier="S",
                    products=[("S", "N-300-M")], registered=True, is_current=True, product_id="GLOVE-A",
                    values={"drugs": ["Cisplatin"], "standard": "ASTM D6978", "results": {"Cisplatin": ">240 min"}})
    return d.model_copy(update=overrides)


SKU_A = ev.SkuRef(sku_id="GLOVE-A-M", product_id="GLOVE-A", supplier=None, catalog_no="N-300-M",
                  mapping_verified=True)
QA = {("RPT-CHEMO-A", 1): ("qa.jane", AT)}


# ---------------------------------------------------------------- the five checks, one test each


def test_all_five_checks_pass_glove_a_is_verified_with_who_and_when():
    v = ev.verify_chemo(glove_a_report(), SKU_A, QA)
    assert v.verified and v.failed == [] and (v.verified_by, v.verified_at) == ("qa.jane", AT)


@pytest.mark.parametrize("check, doc, sku, qa", [
    ("registered", glove_a_report(registered=False), SKU_A, QA),
    ("current", glove_a_report(is_current=False), SKU_A, QA),
    ("this_product", glove_a_report(products=[("S", "N-310-M")]), SKU_A, QA),
    ("this_product", glove_a_report(), SKU_A.model_copy(update={"mapping_verified": False}), QA),
    ("fields", glove_a_report(values={"drugs": ["Cisplatin"], "standard": "ASTM D6978", "results": {}}), SKU_A, QA),
    ("qa_signoff", glove_a_report(), SKU_A, {}),
])
def test_any_one_check_failing_leaves_the_claim_unverified_with_that_reason(check, doc, sku, qa):
    v = ev.verify_chemo(doc, sku, qa)
    assert not v.verified and v.failed == [check]
    row, = ev.chemo_rows(doc, sku, qa, AT)
    assert row.verification_status == Verification.UNVERIFIED and check in row.reason and row.verified_by is None


def test_a_chemo_claim_without_a_drug_list_and_standard_stays_unverified():
    doc = glove_a_report(values={"chemo_claim": "true"})
    assert "fields" in ev.verify_chemo(doc, SKU_A, QA).failed


def test_a_marketing_sheet_never_verifies_glove_b():
    rows, _, _ = md.demo_evidence(signoffs={("MKT-B", 1): "qa.jane"})
    b, = chemo(rows, "GLOVE-B-M", "MKT-B")
    assert b.verification_status == Verification.UNVERIFIED
    assert "registered" in b.reason and "fields" in b.reason


# ---------------------------------------------------------------- versions, units, ranges


def test_a_newer_version_supersedes_older_rows_and_nothing_is_deleted():
    before, _, _ = md.demo_evidence()
    after, _, _ = md.demo_evidence(["spec_v4"])
    a3 = [r for r in after if r.document_id == "SPEC-A" and r.document_version == 3]
    a4 = [r for r in after if r.document_id == "SPEC-A" and r.document_version == 4]
    assert a3 and {r.verification_status for r in a3} == {Verification.SUPERSEDED}
    assert a4 and {r.verification_status for r in a4} == {Verification.VERIFIED}
    assert next(r for r in a4 if r.attribute_name == "thickness" and r.sku_id == "GLOVE-A-M").attribute_value \
        == "0.09"
    assert len(after) > len(before)                                    # v3 is still there, superseded


def test_values_without_a_unit_or_out_of_range_stay_unverified():
    assert ev.check_value("thickness", 0.08, None)[2] == "no unit"
    assert ev.check_value("thickness", 3.1, "mil")[:2] == (0.0787, "mm")       # unit mix-up converted, not ignored
    assert "outside" in ev.check_value("tensile_strength", 140.0, "MPa")[2]
    assert "allowed" in ev.check_value("aql", 1.2, None)[2]
    doc = ev.Document(document_id="SPEC-A", version=3, document_type="spec_sheet", products=[("S", "N-300-M")],
                      registered=True, is_current=True, product_id="GLOVE-A",
                      values={"thickness": "0.08", "aql": 1.5})
    rows = {r.attribute_name: r for r in ev.spec_rows(doc, SKU_A, {("SPEC-A", 3): ("qa", AT)}, AT)}
    assert rows["thickness"].verification_status == Verification.UNVERIFIED and rows["thickness"].reason == "no unit"
    assert rows["aql"].verification_status == Verification.VERIFIED


def test_parse_quantity_and_extracted_strings():
    assert ev.parse_quantity("0.08 mm") == (0.08, "mm") and ev.parse_quantity("14 MPa") == (14.0, "MPa")
    v = ev.parse_extracted({"drugs": "Cisplatin, Etoposide", "results": "Cisplatin: >240 min; Etoposide: 45 min",
                            "aql": "1.5", "catalog_numbers": '["N-300-M"]'})
    assert v["drugs"] == ["Cisplatin", "Etoposide"] and v["results"]["Etoposide"] == "45 min"
    assert v["aql"] == 1.5 and v["catalog_numbers"] == ["N-300-M"]


# ---------------------------------------------------------------- the demo, as the episode runs it


def test_the_tracer_gloves_through_the_episode():
    # before: no report for A (and B never has one)
    rows, _, _ = md.demo_evidence()
    assert chemo(rows, "GLOVE-A-M") == [] and chemo(rows, "GLOVE-B-M", "RPT-CHEMO-B") == []
    # A's report dropped: four checks pass, the QA sign-off is missing
    rows, _, _ = md.demo_evidence(["glove_a_report"])
    a, = chemo(rows, doc="RPT-CHEMO-A")
    assert a.verification_status == Verification.UNVERIFIED and a.reason == "failed: qa_signoff"
    # signed off: verified, with the standard and who signed
    rows, _, _ = md.demo_evidence(["glove_a_report", "other_catalog_report"], A_SIGNED)
    a, = chemo(rows, doc="RPT-CHEMO-A")
    assert a.verification_status == Verification.VERIFIED and a.verified_by == "qa.jane"
    assert a.test_method == md.STANDARD and a.document_version == 1
    # a valid report for another catalog number, filed against Glove A: the this-product check fails
    x, = chemo(rows, doc="RPT-CHEMO-N310")
    assert x.verification_status == Verification.UNVERIFIED and "this_product" in x.reason


def test_zero_verified_rows_from_a_non_current_version_and_zero_without_all_checks():
    rows, _, reg = md.demo_evidence(list(md.SCENARIOS), A_SIGNED | {("RPT-CHEMO-B", 1): "qa.jane"})
    current = {(r["document_id"], r["version"]) for r in reg if r["is_current"]}
    verified = [r for r in rows if r.verification_status == Verification.VERIFIED]
    assert verified and all((r.document_id, r.document_version) in current for r in verified)
    assert all(r.verified_by and r.verified_at for r in verified)


def test_eligibility_reads_the_document_evidence_glove_a_eligible_glove_b_unknown():
    rows, _, _ = md.demo_evidence(["glove_a_report"], A_SIGNED)
    doc_ev = ev.to_domain(rows)
    for letter, want in (("A", Eligibility.ELIGIBLE), ("B", Eligibility.NEEDS_EVIDENCE)):
        pid = f"GLOVE-{letter}"
        base = [e for e in fx.evidence() if e.product_id == pid and e.attribute != "chemo_tested"]
        base += [e for e in fx.evidence() if e.product_id == pid and e.source_id.startswith("DESC")]
        res = check_product(fx.product(letter), fx.lakeside_r1(), base + doc_ev, "ev-test")
        assert res.status == want, (letter, res.checks)


# ---------------------------------------------------------------- the generator self-test


def test_generated_pdfs_carry_their_truth_values():
    for d in md.documents(scenarios=md.SCENARIOS):
        data = md.pdf_bytes(d.lines())
        assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
        text = md.pdf_text(data)
        assert md.DEMO in text and f"Document ID: {d.document_id}" in text
        got = md.read_fields(text)
        if d.document_type == "spec_sheet":
            v = ev.parse_extracted(got)
            assert float(v["aql"]) == d.truth["aql"]
            thick = ev.check_value("thickness", *ev.parse_quantity(v["thickness"]))
            assert thick[0] == pytest.approx(d.truth["thickness_mm"], abs=1e-3)
        if d.document_type == "test_report":
            v = ev.parse_extracted(got)
            assert v["drugs"] == d.truth["drugs"] and v["results"] == d.truth["results"]
            assert v["catalog_numbers"] == d.truth["catalog_numbers"]


def test_the_truth_file_is_what_the_generator_writes():
    path = ROOT / "evaluation" / "quote_ai" / "v1" / "product_facts" / "documents.jsonl"
    assert path.read_text(encoding="utf-8") == md.truth_jsonl(md.documents(scenarios=md.SCENARIOS)), \
        "regenerate: python -c \"from quote_ai.mock import documents as m; ...truth_jsonl...\""
    assert all(json.loads(l)["expected"] for l in path.read_text(encoding="utf-8").splitlines())


def test_unknown_scenarios_are_refused():
    with pytest.raises(ValueError):
        md.documents(scenarios=["glove_z_report"])
