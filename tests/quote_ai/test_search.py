"""Search (AI-15): the search document, hybrid vs vector, filters as pre-filters only, and the shortlist that search
and the rules build together, committed only by QuoteService."""
from pathlib import Path

import pytest

from quote_ai.domain.models import Eligibility, QuoteState as S
from quote_ai.search import docs, local, retriever
from quote_ai.services import QuoteError, QuoteService
from quote_ai.services.shortlist import LocalRules, build_shortlist, view
from quote_ai.storage.evidence_store import DemoEvidenceStore
from quote_ai.storage.product_catalog import DemoProductCatalog
from quote_ai.storage.repository import InMemoryQuoteRepository
import lakeside_fixtures as fx

ROOT = Path(__file__).resolve().parents[2]
DOCS = docs.demo_docs()
INDEX = local.InMemoryIndex(DOCS)


def test_one_search_row_per_sku_with_the_documented_columns():
    assert len({d["sku_id"] for d in DOCS}) == len(DOCS) and set(DOCS[0]) == set(docs.COLUMNS)
    a = next(d for d in DOCS if d["sku_id"] == "GLOVE-A-M")
    assert a["search_text"].count(docs.SEPARATOR) == 3 and "chemotherapy" in a["search_text"]


def test_the_sql_search_document_builds_the_same_text():
    sql = (ROOT / "src/quote_ai/pipelines/search_doc.sql").read_text(encoding="utf-8")
    assert "concat_ws(' | ', p.name, coalesce(d.brand_name" in sql and "device_description" in sql
    assert "MERGE INTO {ai}.sku_search_doc" in sql and "WHEN NOT MATCHED BY SOURCE THEN DELETE" in sql
    for word in ("latex-free", "powder-free", "chemo-tested", "discontinued", "contains latex"):
        assert f"'{word}'" in sql


def test_semantic_search_chemo_no_latex_puts_no_vinyl_on_top():
    hits = retriever.search_candidates(INDEX, "gloves for handling chemotherapy drugs, no latex", k=40)
    top = retriever.candidate_products(hits, 4)
    materials = {d["product_id"]: d["material_family"] for d in DOCS}
    assert {materials[p] for p in top} <= {"nitrile", "neoprene", "polyisoprene"}


def test_hybrid_finds_the_exact_catalog_number_first():
    exact = retriever.search_candidates(INDEX, "DBX-NC-M-100", k=5, query_type="HYBRID", in_distribution=None)
    vector = retriever.search_candidates(INDEX, "DBX-NC-M-100", k=5, query_type="ANN", in_distribution=None)
    assert exact[0]["catalog_number"] == "DBX-NC-M-100"
    assert vector[0]["catalog_number"] != "DBX-NC-M-100"         # an embedded catalog number is a vague vector


def test_size_filter_and_the_discontinued_sku_disappears():
    hits = retriever.search_candidates(INDEX, "nitrile exam chemo-tested", ["M"], k=60)
    assert {h["size"] for h in hits} == {"M"} and "GLOVE-M" not in {h["product_id"] for h in hits}
    everything = retriever.search_candidates(INDEX, "nitrile exam chemo-tested", ["M"], k=60, in_distribution=None)
    assert "GLOVE-M" in {h["product_id"] for h in everything}


def lakeside_quote(svc):
    q = svc.create("Lakeside Regional Clinic", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-1")
    return svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)


def lakeside_shortlist(q):
    store, cat = DemoEvidenceStore(), DemoProductCatalog()
    return build_shortlist(q.requirements, q.lines, local.InMemoryIndex(docs.demo_docs(store.rows)), cat,
                           LocalRules(store, cat))


def test_lakeside_eight_candidates_three_eligible_c_an_alternative():
    svc = QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW)
    q = lakeside_quote(svc)
    s = lakeside_shortlist(q)
    assert len(s.candidates) == 8
    v = view(s)
    assert {e["product_id"] for e in v["eligible"]} == {"GLOVE-A", "GLOVE-C", "GLOVE-D"}
    assert [e["product_id"] for e in v["needs_evidence"]] == ["GLOVE-B"]
    labels = {e["product_id"]: e["fulfilment"]["label"] for e in v["eligible"]}
    assert labels == {"GLOVE-A": "on time", "GLOVE-C": "alternative, needs acceptance", "GLOVE-D": "on time"}
    excluded = {e["product_id"]: e["failed"] for e in v["excluded"]}
    assert "latex_free" in excluded["GLOVE-E"]
    q = svc.shortlist_candidates(q.quote_id, q.revision, "rep.alice", s.results, s.fulfilment,
                                 s.evidence_snapshot, s.inventory_snapshot)
    assert q.state == S.SHORTLISTED and q.evidence_snapshot == "product_evidence@v41"
    assert {c["product_id"] for c in q.candidates if c["status"] == "eligible"} == {"GLOVE-A", "GLOVE-C", "GLOVE-D"}
    event = svc.repo.events(q.quote_id)[-1]
    assert event.action == "shortlist_candidates" and event.detail["on_time"] == ["GLOVE-A", "GLOVE-D"]


def test_quote_service_refuses_an_unknown_passed_off_as_eligible():
    svc = QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW)
    q = lakeside_quote(svc)
    s = lakeside_shortlist(q)
    forged = [r.model_copy(update={"status": Eligibility.ELIGIBLE}) if r.product_id == "GLOVE-B" else r
              for r in s.results]
    with pytest.raises(QuoteError, match="does not follow"):
        svc.shortlist_candidates(q.quote_id, q.revision, "rep.alice", forged, s.fulfilment, "x", "y")
    with pytest.raises(QuoteError, match="exactly the eligible"):
        svc.shortlist_candidates(q.quote_id, q.revision, "rep.alice", s.results, {}, "x", "y")
    nothing = [r for r in s.results if r.status != Eligibility.ELIGIBLE]
    with pytest.raises(QuoteError, match="no eligible glove"):
        svc.shortlist_candidates(q.quote_id, q.revision, "rep.alice", nothing, {}, "x", "y")
    assert svc.repo.get(q.quote_id).state == S.REQUIREMENTS_CONFIRMED          # nothing written


def test_a_draft_cannot_be_shortlisted():
    svc = QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW)
    q = svc.create("Lakeside Regional Clinic", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-2")
    with pytest.raises(Exception):
        svc.shortlist_candidates(q.quote_id, q.revision, "rep.alice", [], {}, "x", "y")
