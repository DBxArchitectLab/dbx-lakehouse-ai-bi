"""The app's Season 4 routes through HTTP with no workspace: Find products (AI-15), the compare view and Ask about
this product (AI-16), and the account filter from the signed-in rep (AI-17)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.app.backend.context import Context  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.llm.requirements_schema import Requirements as Extracted  # noqa: E402
from quote_ai.mock import knowledge  # noqa: E402
from quote_ai.search import docs, local  # noqa: E402
from quote_ai.storage.accounts import DemoAccounts  # noqa: E402
from quote_ai.services import QuoteService  # noqa: E402
from quote_ai.services.shortlist import LocalRules  # noqa: E402
from quote_ai.storage.evidence_store import DemoDocumentStore, DemoEvidenceStore  # noqa: E402
from quote_ai.storage.product_catalog import DemoProductCatalog  # noqa: E402
from quote_ai.storage.repository import InMemoryQuoteRepository  # noqa: E402
from test_quote_service import LAKESIDE_EXTRACTED  # noqa: E402

ALICE = {"X-Forwarded-Email": "rep.alice@dbxlab.example"}
BOB = {"X-Forwarded-Email": "rep.bob@dbxlab.example"}


def make_ctx(**extra) -> Context:
    store, cat = DemoEvidenceStore(), DemoProductCatalog()
    return Context(QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW), cat,
                   lambda text: Extracted.model_validate(LAKESIDE_EXTRACTED), store, DemoDocumentStore(),
                   sku_index=local.InMemoryIndex(docs.demo_docs(store.rows)), rules=LocalRules(store, cat),
                   knowledge_index=local.InMemoryIndex(knowledge.demo_chunks(store.rows), text_column="text",
                                                       primary_key="chunk_id", keyword_columns=()),
                   accounts=DemoAccounts(), **extra)


@pytest.fixture
def client():
    return TestClient(create_app(make_ctx(), serve_frontend=False))


def confirmed(client, who=ALICE, account="Lakeside Regional Clinic"):
    q = client.post("/api/quotes", json={"account": account, "text": fx.LAKESIDE_REQUEST},
                    headers=who).json()
    return client.post(f"/api/quotes/{q['quote_id']}/confirm", headers=who,
                       json={"revision": q["revision"], "quantity_by_size": fx.LAKESIDE_SPLIT, "unit": "box",
                             "gloves_per_unit": 100}).json()


def test_find_products_three_groups_chips_fulfilment_and_shortlisted(client):
    q = confirmed(client)
    r = client.post(f"/api/shortlist/{q['quote_id']}", json={"revision": q["revision"]}, headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["shortlist"]
    assert body["moved"] and body["quote"]["state"] == "SHORTLISTED" and len(s["candidates"]) == 8
    assert [e["product_id"] for e in s["eligible"]] == ["GLOVE-A", "GLOVE-C", "GLOVE-D"]
    for e in s["eligible"]:
        reqs = {c["requirement"]: c["result"] for c in e["checks"]}
        assert reqs["powder_free"] == reqs["intended_use"] == "verified_match"
        assert set(reqs.values()) == {"verified_match"}
    c = next(e for e in s["eligible"] if e["product_id"] == "GLOVE-C")["fulfilment"]
    assert (c["status"], c["now"], c["later"], c["label"]) == ("partial", 250, 150, "alternative, needs acceptance")
    b, = s["needs_evidence"]
    chemo = next(x for x in b["checks"] if x["requirement"] == "chemo_tested")
    assert chemo["result"] == "unknown" and any("ai_extraction, unverified" in cl for cl in chemo["claims"])
    e = next(x for x in s["excluded"] if x["product_id"] == "GLOVE-E")
    assert next(x for x in e["checks"] if x["requirement"] == "latex_free")["result"] == "verified_mismatch"
    events = body["quote"]["events"]
    assert events[-1]["action"] == "shortlist_candidates" and events[-1]["to_state"] == "SHORTLISTED"


def test_refusals(client):
    q = confirmed(client)
    url = f"/api/shortlist/{q['quote_id']}"
    assert client.post(url, json={"revision": q["revision"]}).status_code == 401
    assert client.post(url, json={"revision": q["revision"]}, headers=BOB).status_code == 403     # AI-24
    assert client.post(url, json={"revision": q["revision"] - 1}, headers=ALICE).status_code == 409
    draft = client.post("/api/quotes", json={"account": "Lakeside Regional Clinic", "text": fx.LAKESIDE_REQUEST},
                        headers=ALICE).json()
    assert client.post(f"/api/shortlist/{draft['quote_id']}", json={"revision": draft["revision"]},
                       headers=ALICE).status_code == 409                       # DRAFT -> SHORTLISTED is not allowed
    bare = TestClient(create_app(Context(QuoteService(InMemoryQuoteRepository()), DemoProductCatalog(),
                                         lambda t: Extracted.model_validate(LAKESIDE_EXTRACTED)),
                                 serve_frontend=False))
    q2 = confirmed(bare)
    assert bare.post(f"/api/shortlist/{q2['quote_id']}", json={"revision": q2["revision"]},
                     headers=ALICE).status_code == 503


def shortlisted(client, who=ALICE, account="Lakeside Regional Clinic"):
    q = confirmed(client, who, account)
    return client.post(f"/api/shortlist/{q['quote_id']}", json={"revision": q["revision"]}, headers=who).json()["quote"]


def test_compare_view_values_with_sources_statuses_from_the_rules_and_b_under_needs_evidence(client):
    q = shortlisted(client)
    r = client.get(f"/api/compare/{q['quote_id']}", headers=ALICE)
    assert r.status_code == 200, r.text
    t = r.json()
    assert [c["product_id"] for c in t["columns"]] == ["GLOVE-A", "GLOVE-C", "GLOVE-D"]
    assert {c["product_id"]: c["offer"] for c in t["columns"]}["GLOVE-C"] == "alternative, needs acceptance"
    assert [n["product_id"] for n in t["needs_evidence"]] == ["GLOVE-B"]
    a = t["columns"][0]["specs"]
    assert a["thickness"] == a["thickness"] | {"value": "0.08", "unit": "mm", "status": "verified", "document_id": "SPEC-A",
                                               "document_version": 3}
    assert a["length"]["status"] == "unverified" and "score" not in r.text.lower()


def test_ask_build_every_statement_cited_and_layered_then_cached(client):
    q = shortlisted(client)
    url = f"/api/compare/{q['quote_id']}/ask"
    r = client.post(url, json={"question": "Explain the differences for the customer"}, headers=ALICE).json()
    assert r["statements"] and all(s["citation"] and s["layer"] for s in r["statements"]) and not r["cached"]
    again = client.post(url, json={"question": "Explain the differences for the customer"}, headers=ALICE).json()
    assert again["cached"] and again["answer"] == r["answer"]
    a = client.post(url, json={"question": "What supports the chemo claim?", "product_id": "GLOVE-A"},
                    headers=ALICE).json()
    assert any(s["layer"] == "business_truth" and "RPT-CHEMO-A" in s["citation"] for s in a["statements"])
    b = client.post(url, json={"question": "Can we offer it for chemo use?", "product_id": "GLOVE-B"},
                    headers=ALICE).json()
    assert any(s["layer"] == "extracted_claim" for s in b["statements"])
    assert any("no chemo test report on file" in s["text"] for s in b["statements"])
    none = client.post(url, json={"question": "Shelf life in tropical storage?", "product_id": "GLOVE-A"},
                       headers=ALICE).json()
    assert none["answer"] == "no evidence on file" and none["sources"] == []


def test_ask_refusals(client):
    q = shortlisted(client)
    url = f"/api/compare/{q['quote_id']}/ask"
    assert client.post(url, json={"question": "x", "product_id": "GLOVE-E"}, headers=ALICE).status_code == 422
    assert client.post(url, json={"question": "x", "variant": "buy"}, headers=ALICE).status_code == 503
    fresh = confirmed(client)
    assert client.get(f"/api/compare/{fresh['quote_id']}", headers=ALICE).status_code == 409


def test_buy_goes_through_the_same_grounding():
    from types import SimpleNamespace
    resp = SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(
        text="Glove A is tested to ASTM D6978.", annotations=[SimpleNamespace(filename="RPT-CHEMO-A_v1.pdf")])])])
    c = TestClient(create_app(make_ctx(ka=lambda question: resp), serve_frontend=False))
    q = shortlisted(c)
    r = c.post(f"/api/compare/{q['quote_id']}/ask", json={"question": "What supports the chemo claim?",
                                                          "variant": "buy"}, headers=ALICE).json()
    assert r["variant"] == "buy" and r["statements"][0]["layer"] == "business_truth"


def test_permission_rep_north_never_sees_lakeside_price_sheet(client):
    """AI-17: the accounts come from the signed-in identity and filter the index before retrieval."""
    question = {"question": "What did we agree on the Lakeside price sheet?"}
    north = shortlisted(client, BOB, "Harbor Point Dental Group")  # rep_north: Harbor Point, Summit
    r = client.post(f"/api/compare/{north['quote_id']}/ask", json=question, headers=BOB).json()
    assert r["answer"] == "no evidence on file" and r["sources"] == []
    assert not any(c["account_id"] == "ACC-001" for c in r["retrieved"])
    south = shortlisted(client, ALICE)                           # rep_south: Lakeside
    r = client.post(f"/api/compare/{south['quote_id']}/ask", json=question, headers=ALICE).json()
    assert any(s.startswith("PS-ACC-001") for s in r["sources"]) and "service principal" in r["asked_as"]
