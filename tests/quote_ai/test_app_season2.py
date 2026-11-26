"""The app's Season 2 routes (AI-08 to AI-11), through HTTP with no workspace: the Product page's evidence badges, the
Documents tab, a request uploaded as a PDF, My pipeline, and Ask the data."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.app.backend.context import Context  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.llm.requirements_schema import Requirements as Extracted  # noqa: E402
from quote_ai.mock import documents as md  # noqa: E402
from quote_ai.services import QuoteService  # noqa: E402
from quote_ai.storage.evidence_store import DemoDocumentStore, DemoEvidenceStore  # noqa: E402
from quote_ai.storage.product_catalog import DemoProductCatalog  # noqa: E402
from quote_ai.storage.repository import InMemoryQuoteRepository  # noqa: E402
from test_quote_service import LAKESIDE_EXTRACTED  # noqa: E402

ALICE = {"X-Forwarded-Email": "rep.alice@dbxlab.example"}
SEEN = []


def extractor(text):
    SEEN.append(text)
    return Extracted.model_validate(LAKESIDE_EXTRACTED)


def genie(question):
    return {"text": "Lakeside bought 3 SKUs.", "sql": "SELECT ...", "columns": ["sku_id", "boxes"],
            "rows": [["GLOVE-A-M", 200]], "status": "COMPLETED"}


@pytest.fixture
def client():
    ctx = Context(QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW), DemoProductCatalog(), extractor,
                  DemoEvidenceStore(), DemoDocumentStore(),
                  lambda: {"url": "https://host/embed/dashboardsv3/d1", "dashboard_id": "d1", "reason": ""}, genie)
    return TestClient(create_app(ctx, serve_frontend=False))


def by(rows, attr, source=None):
    return [r for r in rows if r["attribute_name"] == attr and (source is None or r["source_type"] == source)]


def test_product_page_glove_a_latex_free_green_chemo_claim_amber_and_the_report_verified(client):
    r = client.get("/api/evidence/GLOVE-A-M", headers=ALICE)
    assert r.status_code == 200
    rows = r.json()["rows"]
    latex, = by(rows, "latex_free")
    assert (latex["verification_status"], latex["source_type"], latex["extraction_method"]) == \
        ("verified", "gudid", "structured_field")
    ai, = by(rows, "chemo_tested", "ai_extraction")
    assert ai["verification_status"] == "unverified" and ai["extraction_method"] == "ai_function"
    doc, = by(rows, "chemo_tested", "supplier_document")
    assert doc["verification_status"] == "verified" and doc["document_id"] == "RPT-CHEMO-A"


def test_glove_b_has_the_same_claim_and_no_report(client):
    rows = client.get("/api/evidence/GLOVE-B-M", headers=ALICE).json()["rows"]
    assert [r["verification_status"] for r in by(rows, "chemo_tested", "ai_extraction")] == ["unverified"]
    assert all(r["verification_status"] != "verified" for r in by(rows, "chemo_tested"))


def test_glove_f_shows_the_conflict(client):
    rows = client.get("/api/evidence/GLOVE-F-M", headers=ALICE).json()["rows"]
    assert {r["verification_status"] for r in by(rows, "latex_free")} == {"conflicting"}


def test_evidence_needs_a_signed_in_rep_and_a_known_product(client):
    assert client.get("/api/evidence/GLOVE-A").status_code == 401
    assert client.get("/api/evidence/GLOVE-Z", headers=ALICE).status_code == 404


def test_documents_tab_glove_a_report_verified_glove_b_none(client):
    a = client.get("/api/documents/GLOVE-A", headers=ALICE).json()["documents"]
    rpt, = [d for d in a if d["document_id"] == "RPT-CHEMO-A"]
    assert rpt["verified"] == ["chemo_tested"] and rpt["verified_by"] == "qa.demo"
    spec2 = next(d for d in a if d["document_id"] == "SPEC-A" and d["version"] == 2)
    assert not spec2["is_current"] and "thickness" in spec2["superseded"]
    b = client.get("/api/documents/GLOVE-B", headers=ALICE).json()["documents"]
    assert not [d for d in b if d["document_type"] == "test_report"]
    mkt, = [d for d in b if d["document_id"] == "MKT-B"]
    assert mkt["verified"] == []


def test_a_request_pdf_gives_the_same_draft_as_the_email(client):
    pdf = md.pdf_bytes(md.Doc(document_id="REQ", version=1, document_type="request", product_id=None,
                              effective_date=fx.NOW.date(), body=[md.LAKESIDE_REQUEST]).lines())
    r = client.post("/api/documents/request?account=Lakeside%20Regional%20Clinic", content=pdf,
                    headers=ALICE | {"Content-Type": "application/pdf"})
    assert r.status_code == 201, r.text
    q = r.json()
    assert q["state"] == "DRAFT" and q["requirements"]["missing"] == ["quantity_per_size", "gloves_per_unit"]
    assert q["document_path"].endswith(".pdf") and "/requests/" in q["document_path"]
    assert SEEN[-1].strip() == md.LAKESIDE_REQUEST                   # the parsed text, header lines dropped
    assert client.post("/api/documents/request?account=x", content=b"hello",
                       headers=ALICE | {"Content-Type": "application/pdf"}).status_code == 415


def test_my_pipeline_returns_the_embed_url_and_the_rep(client):
    r = client.get("/api/pipeline", headers=ALICE).json()
    assert r["url"].endswith("/embed/dashboardsv3/d1") and r["rep"] == "rep.alice"


def test_ask_the_data_answer_table_and_sql(client):
    r = client.post("/api/ask-data", json={"question": "What has Lakeside bought in the last year?"}, headers=ALICE)
    assert r.status_code == 200
    a = r.json()
    # AI-24: Genie runs as the signed-in rep: Lakeside's price, never the cost or the margin
    assert a["sql"] and a["columns"] == ["account", "avg_price", "avg_cost", "margin_pct"]
    assert [r[0] for r in a["rows"]] == ["Lakeside Regional Clinic"] and a["rows"][0][2:] == [None, None]
    assert a["asked_as"] == "you (rep.alice@dbxlab.example)"


def test_season_2_routes_refuse_cleanly_when_not_configured():
    ctx = Context(QuoteService(InMemoryQuoteRepository()), DemoProductCatalog(), extractor, as_user=None)
    c = TestClient(create_app(ctx, serve_frontend=False))
    assert c.post("/api/ask-data", json={"question": "win rate?"}, headers=ALICE).status_code == 503
    assert c.get("/api/evidence/GLOVE-A", headers=ALICE).status_code == 503
    assert c.get("/api/pipeline", headers=ALICE).json()["url"] is None
