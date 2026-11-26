"""The QuoteAI v1 back end (AI-07), through HTTP: the Lakeside path as a rep, and every refusal. No workspace: an
in-memory QuoteService, the synthetic catalog and a fake extraction."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.app.backend.context import Context  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.llm.extract import ExtractionError  # noqa: E402
from quote_ai.llm.requirements_schema import Requirements as Extracted  # noqa: E402
from quote_ai.services import QuoteService  # noqa: E402
from quote_ai.storage.product_catalog import DemoProductCatalog  # noqa: E402
from quote_ai.storage.repository import InMemoryQuoteRepository  # noqa: E402
from test_quote_service import LAKESIDE_EXTRACTED  # noqa: E402

ALICE = {"X-Forwarded-Email": "rep.alice@dbxlab.example"}
BOB = {"X-Forwarded-Email": "rep.bob@dbxlab.example"}


def extractor(text):
    if "garbage" in text:
        raise ExtractionError("the answer does not match the requirements schema")
    return Extracted.model_validate(LAKESIDE_EXTRACTED)


@pytest.fixture
def client():
    ctx = Context(QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW), DemoProductCatalog(), extractor)
    return TestClient(create_app(ctx, serve_frontend=False))


def read(client, headers=ALICE):
    r = client.post("/api/quotes", json={"account": "Lakeside Regional Clinic", "text": fx.LAKESIDE_REQUEST},
                    headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def confirm(client, q, split=None, headers=ALICE):
    return client.post(f"/api/quotes/{q['quote_id']}/confirm", headers=headers,
                       json={"revision": q["revision"], "quantity_by_size": split or fx.LAKESIDE_SPLIT, "unit": "box",
                             "gloves_per_unit": 100})


def test_the_lakeside_path_as_a_rep(client):
    q = read(client)
    assert q["state"] == "DRAFT" and q["rep"] == "rep.alice@dbxlab.example"
    assert q["requirements"]["missing"] == ["quantity_per_size", "gloves_per_unit"]
    assert q["extracted"]["chemo_tested"] == {"value": True, "hard": True} and q["extracted"]["sterile"]["value"] is None

    r = confirm(client, q, {"S": 50, "M": 140, "L": 140, "XL": 60})                    # 50 for S by mistake
    assert r.status_code == 422 and "add up to 390" in r.json()["detail"]

    r = confirm(client, q)
    q = r.json()
    assert r.status_code == 200 and q["state"] == "REQUIREMENTS_CONFIRMED" and len(q["lines"]) == 4
    assert q["requirements"]["version"] == 2

    r = client.post(f"/api/quotes/{q['quote_id']}/quick-quote", json={"revision": q["revision"]}, headers=ALICE)
    q = r.json()
    assert r.status_code == 200 and q["state"] == "PRICED" and q["label"] == "v1: simple rules, demo data"
    assert q["total"] == 3440.0 and q["checks"]["chemo_tested"] == "not checked in v1"
    assert [e["to_state"] for e in q["events"]] == ["DRAFT", "REQUIREMENTS_CONFIRMED", "SHORTLISTED", "PRICED"]

    mine = client.get("/api/quotes", headers=ALICE).json()
    assert [m["quote_id"] for m in mine] == [q["quote_id"]] and len(mine[0]["events"]) == 4


def test_a_jump_the_rules_forbid_is_409_and_writes_nothing(client):
    q = confirm(client, read(client)).json()
    r = client.post(f"/api/quotes/{q['quote_id']}/state", json={"revision": q["revision"], "to": "ISSUED"},
                    headers=ALICE)
    assert r.status_code == 409 and r.json()["detail"] == "REQUIREMENTS_CONFIRMED -> ISSUED is not allowed"
    after = client.get(f"/api/quotes/{q['quote_id']}", headers=ALICE).json()
    assert after["revision"] == q["revision"] and len(after["events"]) == 2


def test_a_stale_screen_gets_409(client):
    q = read(client)
    assert confirm(client, q).status_code == 200
    r = confirm(client, q)                                   # the same screen, submitted again
    assert r.status_code == 409 and "revision" in r.json()["detail"]


def test_no_sign_in_is_401_and_another_accounts_quote_is_403(client):
    """AI-24: not a filter by rep in code any more; Bob may not read Lakeside's account, so: no access."""
    assert client.get("/api/quotes").status_code == 401
    q = read(client)
    r = client.get(f"/api/quotes/{q['quote_id']}", headers=BOB)
    assert r.status_code == 403 and r.json()["detail"] == "no access to this account"
    assert confirm(client, q, headers=BOB).status_code == 403
    assert client.post("/api/quotes", json={"account": "Lakeside Regional Clinic", "text": "x"},
                       headers=BOB).status_code == 403


def test_an_extraction_outside_the_schema_is_502_and_no_quote(client):
    r = client.post("/api/quotes", json={"account": "Lakeside Regional Clinic", "text": "garbage"}, headers=ALICE)
    assert r.status_code == 502
    assert client.get("/api/quotes", headers=ALICE).json() == []


def test_product_lookup(client):
    r = client.get("/api/products/lookup", params={"catalog_number": "DBX-NE-M-100"}, headers=ALICE)
    assert r.status_code == 200 and r.json()["skus"][0]["gloves_per_unit"] == 100
    r = client.get("/api/products/lookup", params={"catalog_number": "dbx-ne-m-200"}, headers=ALICE)
    assert r.json()["skus"][0]["sku_id"] == "GLOVE-F-M" and r.json()["skus"][0]["gloves_per_unit"] == 200
    assert client.get("/api/products/lookup", params={"catalog_number": "NOPE"}, headers=ALICE).status_code == 404


def test_the_app_imports_without_a_workspace():
    from quote_ai.app.backend import main
    assert main.app.state.ctx is None            # built on first request, from the app's environment
    assert TestClient(main.app).get("/api/health").json() == {"status": "ok"}
