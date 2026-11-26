"""The capstone (AI-27): the whole Lakeside transaction (plan 4.6), request -> ISSUED, through the app's own API on the
demo data with the stand-in model, after Glove B's report arrived; and the scorecard that maps every success
criterion of the plan (section 10) to the suite or test that proves it."""
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

ROOT = Path(__file__).resolve().parents[2]
CAPSTONE = ROOT / "evaluation" / "quote_ai" / "capstone"
sys.path.insert(0, str(CAPSTONE))
sys.path.insert(0, str(CAPSTONE.parent))
import app_flow  # noqa: E402
import run_capstone  # noqa: E402

SECTION_10 = ["Eligibility", "Fulfilment", "Mapping", "Product facts", "Pricing", "Workflow", "Requirement extraction",
              "Attribute extraction", "Search", "Comparison", "Genie analytics", "Win prediction", "Security",
              "Operations", "Business outcome", "App"]


@pytest.fixture(scope="module")
def flow():
    return app_flow.run()


def test_the_lakeside_request_becomes_an_issued_quote_in_the_app(flow):
    assert flow["failed"] == [] and flow["state"] == "ISSUED"
    assert all(s["route"].split()[1].startswith("/api/") for s in flow["steps"])          # no notebook: the API


def test_a_b_c_d_eligible_e_excluded_and_c_the_alternative(flow):
    assert {"GLOVE-A", "GLOVE-B", "GLOVE-C", "GLOVE-D"} <= set(flow["eligible"]) and "GLOVE-E" in flow["excluded"]
    assert flow["fulfilment"]["GLOVE-A"] == flow["fulfilment"]["GLOVE-D"] == "full"
    assert flow["fulfilment"]["GLOVE-C"] == "partial"


def test_every_state_change_went_through_quote_service_in_order(flow):
    assert flow["events"] == ["create", "confirm", "shortlist_candidates", "shortlist", "price", "submit", "approve",
                              "issue"]


def test_glove_b_unknown_before_its_report():
    import security
    from quote_ai.fixtures.lakeside import LAKESIDE_REQUEST
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    client, _ = security.app(store=DemoEvidenceStore())        # the evidence before AI-26
    q = security.shortlisted(client, "rep_a", "Lakeside Regional Clinic", LAKESIDE_REQUEST)
    assert {c["product_id"]: c["status"] for c in q["candidates"]}["GLOVE-B"] == "needs_evidence"


def test_the_scorecard_covers_every_criterion_of_section_10():
    assert [c[0] for c in run_capstone.CRITERIA] == SECTION_10
    import gate
    suites = gate.load_thresholds()
    for area, _, proofs in run_capstone.CRITERIA:
        for suite, metric in proofs:
            if suite in suites:
                assert metric in suites[suite]["thresholds"], f"{area}: {suite}.{metric}"


def test_the_local_scorecard_passes_and_names_what_needs_the_workspace(capsys):
    assert run_capstone.main(["--baseline-minutes", "95", "--app-minutes", "6"]) == 0
    out = capsys.readouterr().out
    assert "CAPSTONE PASSED" in out
    rows = run_capstone.scorecard({**run_capstone._suite_checks(run_capstone.parser().parse_args([]), {}),
                                   **run_capstone.production(run_capstone.parser().parse_args([]))})
    by = {r["area"]: r for r in rows}
    assert by["Eligibility"]["status"] == "pass" and by["Search"]["status"] == "pass"
    assert by["Genie analytics"]["status"] == "not_run" and "genie.benchmark" in by["Genie analytics"]["not_run"]
    assert by["Requirement extraction"]["status"] == "not_run"       # needs the extraction model: never a pass here


def test_a_failing_criterion_fails_the_capstone():
    checks = {("eligibility", "unknown_as_match"): {"value": 1, "status": "fail"}}
    rows = {r["area"]: r for r in run_capstone.scorecard(checks)}
    assert rows["Eligibility"]["status"] == "fail" and rows["Fulfilment"]["status"] == "not_run"


def test_the_workspace_suites_are_read_from_the_latest_gate_run():
    prior = {("genie", "benchmark"): {"value": 0.925, "status": "pass"}}
    checks = run_capstone._suite_checks(run_capstone.parser().parse_args([]), prior)
    assert checks[("genie", "benchmark")] == {"value": 0.925, "status": "pass"}


def test_the_capstone_job_is_read_only_and_in_the_bundle():
    import yaml
    job = yaml.safe_load((ROOT / "resources" / "quote_ai" / "quote_ai_capstone.job.yml").read_text(
        encoding="utf-8"))["resources"]["jobs"]["quote_ai_capstone"]
    params = job["tasks"][0]["spark_python_task"]["parameters"]
    assert job["tags"]["app"] == "quote_ai" and "--results-table" in params and "--table" in params


def test_the_twelve_decision_records_exist():
    names = sorted(p.name for p in (ROOT / "docs" / "quote_ai" / "decisions").glob("00*.md"))
    numbers = sorted({n[:4] for n in names})
    assert numbers == [f"{i:04d}" for i in range(1, 13)]
    for n in names:
        text = (ROOT / "docs" / "quote_ai" / "decisions" / n).read_text(encoding="utf-8")
        assert "Status" in text and "## Context" in text, n
