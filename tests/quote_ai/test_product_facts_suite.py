"""The product facts suite's scoring (AI-08, AI-09), with no model: a predictor that answers the hand labels scores
100% and writes no status violation; a wrong material or a missed unit conversion is counted. The document truth is
read back from the generated PDFs."""
import json
import sys
from pathlib import Path

from quote_ai.mock import documents as md

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation" / "quote_ai"))
import run_eval  # noqa: E402

CASES = run_eval.load("product_facts")


def test_100_labelled_cases_with_the_attribute_fields():
    assert len(CASES) == 100
    assert all(set(run_eval.ATTRIBUTE_FIELDS) <= set(c["expected"]) for c in CASES)
    assert sum(c["expected"]["chemo_claim"] is True for c in CASES) >= 10


def test_a_perfect_reader_scores_100_and_writes_no_violation():
    labels = {c["description"]: c["expected"] for c in CASES}
    r = run_eval.score_attributes(CASES, lambda d: labels[d])
    assert set(r["accuracy"].values()) == {1.0} and r["status_violations"] == [] and r["failures"] == []


def test_a_wrong_material_is_counted_against_that_attribute_only():
    labels = {c["description"]: c["expected"] for c in CASES}
    r = run_eval.score_attributes(CASES[:10], lambda d: labels[d] | {"material": "vinyl"})
    assert r["accuracy"]["material"] < 1.0 and r["accuracy"]["powder_free"] == 1.0


def test_document_field_accuracy_against_the_truth_file():
    truth = run_eval.load("product_facts", "documents.jsonl")
    docs = md.documents(scenarios=md.SCENARIOS)
    values = {f"/Volumes/x/y/z/{d.path}": md.read_fields(md.pdf_text(md.pdf_bytes(d.lines()))) for d in docs}
    r = run_eval.score_documents(truth, values)
    assert r["failures"] == [] and set(r["accuracy"].values()) == {1.0}
    # thickness written in mil but read as mm: the unit mix-up the episode measures
    spec_d = next(d for d in docs if d.document_id == "SPEC-D")
    values[f"/Volumes/x/y/z/{spec_d.path}"]["thickness"] = "3.1 mm"
    r = run_eval.score_documents(truth, values)
    assert r["accuracy"]["thickness_mm"] < 1.0
    assert json.dumps(r)          # what the runner prints
