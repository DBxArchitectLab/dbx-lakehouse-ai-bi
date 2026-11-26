"""The scorers themselves (AI-17): a bug in a scorer would hide real bugs. Toy data first, then the three suites as
files (well formed, every contract requirement covered, every case pinned to a snapshot) and as runs: the gates hold
at zero, and the deliberate breaks of the demo turn them red."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation" / "quote_ai"))
import scorers  # noqa: E402
import season4  # noqa: E402


def check(req, result, evidence=()):
    return {"requirement": req, "result": result, "evidence": list(evidence)}


# ------------------------------------------------------------------ toy data

def test_recall_at_5():
    assert scorers.recall_at_k(["A", "X", "C", "Y", "Z", "D"], ["A", "C", "D"], 5) == pytest.approx(2 / 3)
    assert scorers.recall_at_5({"sku_ids": ["A", "C", "D"]}, {"expected_skus": ["A", "C", "D"]}) == 1.0
    assert scorers.recall_at_5({"sku_ids": []}, {"expected_skus": []}) == 1.0
    assert scorers.recall_at_5({"sku_ids": ["B"]}, {"expected_skus": ["A"]}) == 0.0


def test_eligibility_violations_count_each_bad_product_once():
    ok = {"product_id": "A", "status": "eligible", "checks": [check("latex_free", "verified_match", ["g:1"])]}
    latex = {"product_id": "E", "status": "eligible", "checks": [check("latex_free", "verified_match", ["g:2"])]}
    unknown = {"product_id": "B", "status": "eligible", "checks": [check("chemo_tested", "unknown")]}
    claim = {"product_id": "Q", "status": "eligible", "checks": [check("chemo_tested", "verified_match", ["ai:9"])]}
    excluded = {"product_id": "K", "status": "excluded", "checks": [check("latex_free", "verified_mismatch")]}
    out = {"results": [ok, latex, unknown, claim, excluded], "unverified_sources": ["ai:9"]}
    assert scorers.eligibility_violations(out, {"must_not_include_products": ["E", "K"]}) == 3
    assert scorers.eligibility_violations({"results": [ok, excluded]}, {"must_not_include_products": ["K"]}) == 0


def test_eligibility_case_failures_every_requirement_and_the_status():
    result = {"status": "needs_evidence", "checks": [check("latex_free", "verified_match"),
                                                     check("chemo_tested", "unknown")]}
    exp = {"expect": {"latex_free": "M", "chemo_tested": "?"}, "status": "needs_evidence"}
    assert scorers.eligibility_case_failures({"result": result}, exp) == 0
    assert scorers.eligibility_case_failures({"result": result}, exp | {"status": "eligible"}) == 1
    assert scorers.eligibility_case_failures({"result": result},
                                             exp | {"expect": {"latex_free": "M", "chemo_tested": "M"}}) == 1
    assert scorers.eligibility_case_failures({"result": result}, exp | {"expect": {"latex_free": "M"}}) == 1  # extra


def test_truth_layer_violations():
    st = [{"text": "t", "citation": "RPT-CHEMO-A:1:3", "layer": "business_truth"},
          {"text": "t", "citation": "GUDID-DESC-B:1:0", "layer": "extracted_claim"},
          {"text": "t", "citation": "", "layer": "business_truth"},
          {"text": "t", "citation": "MKT-B:1:3", "layer": "business_truth"},
          {"text": "t", "citation": "table:GLOVE-A:aql", "layer": ""}]
    out = {"statements": st, "verified_sources": ["RPT-CHEMO-A:1:3"]}
    assert scorers.truth_layer_violations(out, {}) == 3


def test_citation_accuracy_needs_the_source_to_support_the_statement():
    texts = {"R:1:3": "Drugs tested\nCarmustine: 45 min\nCisplatin: >240 min"}
    good = {"text": "Carmustine: 45 min", "citation": "R:1:3", "layer": "business_truth"}
    wrong = {"text": "Thickness 0.08 mm", "citation": "R:1:3", "layer": "business_truth"}
    assert scorers.citation_accuracy({"statements": [good], "source_texts": texts}, {}) == 1.0
    assert scorers.citation_accuracy({"statements": [good, wrong], "source_texts": texts}, {}) == 0.5
    assert scorers.citation_accuracy({"statements": [], "source_texts": {}}, {}) == 1.0


def test_source_recall_cross_account_and_no_evidence():
    out = {"statements": [{"text": "x", "citation": "RPT-CHEMO-A:1:3", "layer": "business_truth"}],
           "answer": "Glove A ...", "retrieved": [{"chunk_id": "PS-ACC-001:1:1", "account_id": "ACC-001"},
                                                 {"chunk_id": "PLAYBOOK:1:0", "account_id": "public"}]}
    assert scorers.source_recall(out, {"expected_sources": ["RPT-CHEMO-A", "SPEC-A"]}) == 0.5
    assert scorers.cross_account_violations(out, {"allowed_accounts": ["ACC-002"]}) == 1
    assert scorers.cross_account_violations(out, {"allowed_accounts": ["ACC-001"]}) == 0
    assert scorers.no_evidence_failures(out, {"expect_no_evidence": True}) == 1
    assert scorers.no_evidence_failures({"answer": "no evidence on file"}, {"expect_no_evidence": True}) == 0


# ------------------------------------------------------------------ the suites as files

CONTRACT = {"material", "powder_free", "intended_use", "latex_free", "chemo_tested", "sterile", "sizes",
            "in_distribution"}


def test_eligibility_suite_60_cases_every_requirement_every_snapshot():
    everything = season4.load("eligibility")
    assert [c["case_id"] for c in everything if c.get("kind") == "shortlist"] == ["elig-007"]   # AI-22, Lakeside
    cases = [c for c in everything if c.get("kind") != "shortlist"]
    assert len(cases) == 60 and len({c["case_id"] for c in cases}) == 60
    assert all(c["evidence_snapshot"] in season4.SNAPSHOTS for c in cases)
    assert set().union(*(c["expect"] for c in cases)) == CONTRACT
    b41, b57 = [c for c in cases if c["case_id"] in ("E-014", "E-015")]
    assert (b41["glove"], b41["status"], b41["expect"]["chemo_tested"]) == ("B", "needs_evidence", "?")
    assert (b57["glove"], b57["evidence_snapshot"], b57["status"]) == ("B", "product_evidence@v57", "eligible")
    assert any(c["glove"] == "E" and c["expect"].get("latex_free") == "X" for c in cases)        # the latex trap
    assert any(c["glove"] == "M" and c["expect"]["in_distribution"] == "X" for c in cases)       # discontinued


def test_search_suite_50_requests_lakeside_first():
    everything = season4.load("search")
    cases = [c for c in everything if c.get("split") != "heldout"]
    assert len(cases) == 50
    heldout = [c for c in everything if c.get("split") == "heldout"]       # AI-23 (package 1.1.0): messy requests
    assert len(heldout) == 12 and all(c["from_case"] in {x["case_id"] for x in cases} for c in heldout)
    s1 = cases[0]
    assert s1["need"] == "powder-free nitrile exam gloves, chemo-tested, latex-free" and s1["account_id"] == "ACC-001"
    assert s1["expected_skus"] == ["GLOVE-A-S", "GLOVE-C-S", "GLOVE-D-S"]
    assert {"GLOVE-B-S", "GLOVE-E-S", "GLOVE-M-S"} <= set(s1["must_not_include"])
    assert any("dental" in c["need"] for c in cases) and any("sterile surgical" in c["need"] for c in cases)


def test_comparison_suite_30_questions():
    cases = season4.load("comparison")
    assert len(cases) == 30 and sum(c["episode"] == "AI-16" for c in cases) == 20
    assert {c["rep"] for c in cases} == {"rep.alice", "rep.bob"}


# ------------------------------------------------------------------ the runs: gates at zero, breaks turn red

@pytest.mark.parametrize("suite", ["eligibility", "fulfilment"])
def test_pure_code_suites_pass_with_zero_failures(suite):
    r = season4.run(suite, "")
    assert r["passed"] and set(r["gates"].values()) == {0}


def test_search_gate_holds_for_every_variant_and_recall_is_measured():
    for variant in ("baseline", "hybrid", "hybrid_section", "hybrid_rerank"):
        r = season4.run("search", variant)
        assert r["gates"] == {"eligibility_violations": 0}, variant
        assert 0.5 <= r["metrics"]["recall_at_5"] <= 1.0
    lakeside = season4.predict_search(season4.load("search")[0], "hybrid_section")
    assert scorers.recall_at_5(lakeside, {"expected_skus": ["GLOVE-A-S", "GLOVE-C-S", "GLOVE-D-S"]}) == 1.0


def test_better_search_with_looser_rules_is_rejected():
    """The variant that lets the search document's claims decide: Glove B shows up eligible -> red."""
    r = season4.run("search", "leaky_summary")
    assert not r["passed"] and r["gates"]["eligibility_violations"] > 0
    b = season4.predict_search(season4.load("search")[0], "leaky_summary")
    assert {x["product_id"]: x["status"] for x in b["results"]}["GLOVE-B"] == "eligible"


def test_relaxing_the_latex_check_turns_the_eligibility_suite_red():
    r = season4.run("eligibility", "relax_latex")
    assert not r["passed"] and r["gates"]["eligibility_violations"] >= 1


def test_comparison_gates_hold_and_rep_north_never_sees_lakeside():
    r = season4.run("comparison", "hybrid_section")
    assert r["passed"], r["failing_cases"]
    c21 = next(c for c in season4.load("comparison") if c["case_id"] == "C-021")
    out = season4.predict_comparison(c21)
    assert out["answer"] == "no evidence on file" and not any(x["account_id"] == "ACC-001" for x in out["retrieved"])


def test_the_eval_tables_job_loads_every_case_as_a_row():
    sys.path.insert(0, str(ROOT / "src" / "quote_ai" / "jobs"))
    import eval_job
    rows = eval_job.rows_for("eligibility")
    assert len(rows) == 61 and rows[13][:5] == ("E-014", "v1", "B", "product_evidence@v41", "needs_evidence")
    assert [len(eval_job.rows_for(s)) for s in ("search", "comparison")] == [62, 30]


# ------------------------------------------------------------------ AI-22: the scorers the release rule adds

def option(sku, *states):
    return {"sku_id": sku, "checks": [{"state": s, "counted_as_match": c} for s, c in states]}


def test_no_ineligible_options():
    exp = {"must_exclude_skus": ["GLOVE-E-M"]}
    assert scorers.no_ineligible_options({"shortlist": [option("GLOVE-A-M", ("verified_match", True))]}, exp)
    assert not scorers.no_ineligible_options({"shortlist": [option("GLOVE-E-M", ("verified_match", True))]}, exp)
    # unknown shown as unknown is fine; unknown counted as a match is not
    assert scorers.no_ineligible_options({"shortlist": [option("GLOVE-B-M", ("unknown", False))]}, exp)
    assert not scorers.no_ineligible_options({"shortlist": [option("GLOVE-B-M", ("unknown", True))]}, exp)


def test_unknown_as_match_counts_checks_on_eligible_products():
    out = {"results": [{"product_id": "B", "status": "eligible", "checks": [check("chemo_tested", "unknown"),
                                                                            check("material", "unknown")]},
                       {"product_id": "C", "status": "needs_evidence", "checks": [check("chemo_tested", "unknown")]}]}
    assert scorers.unknown_as_match(out, {}) == 2


def test_layer_cited_is_a_share():
    st = [{"text": "a", "citation": "X", "layer": "source_truth"}, {"text": "b", "citation": "", "layer": "x"}]
    assert scorers.layer_cited({"statements": st}, {}) == 0.5
    assert scorers.layer_cited({"statements": []}, {}) == 1.0


def test_judge_human_agreement():
    assert scorers.judge_human_agreement([]) is None                       # nobody labelled yet: not a pass
    assert scorers.judge_human_agreement([(True, True), (True, False), (False, False), (True, True)]) == 0.75


def test_tool_order_ok_and_the_trace_version():
    from types import SimpleNamespace as NS
    assert scorers.tool_order_ok({"tools": ["search_candidates", "check_eligibility", "price_quote"]}, {}) == 1.0
    assert scorers.tool_order_ok({"tools": ["price_quote", "check_eligibility"]}, {}) == 0.0
    assert scorers.tool_order_ok({"tools": ["get_device"]}, {}) == 1.0
    trace = NS(search_spans=lambda: [NS(name=n) for n in ("agent", "check_eligibility", "knowledge_search")])
    assert scorers.eligibility_before_compare(trace)
    trace = NS(search_spans=lambda: [NS(name=n) for n in ("knowledge_search", "check_eligibility")])
    assert not scorers.eligibility_before_compare(trace)
    assert not scorers.eligibility_before_compare(NS(search_spans=lambda: []))


def test_illegal_transitions():
    ok = {"moves": [(None, "DRAFT"), ("DRAFT", "REQUIREMENTS_CONFIRMED")]}
    assert scorers.illegal_transitions(ok, {}) == 0
    assert scorers.illegal_transitions({"moves": [("SHORTLISTED", "ISSUED")]}, {}) == 1
