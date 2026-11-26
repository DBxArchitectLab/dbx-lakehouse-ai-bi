"""The app's Season 3 route (AI-14), through HTTP with no workspace: the Lakeside quote's win chance at three prices,
one call to the endpoint, labelled a simulation, and nothing on the quote changes (the price still comes from the
price book and the policy)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.app.backend.context import Context  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.llm.requirements_schema import Requirements as Extracted  # noqa: E402
from quote_ai.services import QuoteService  # noqa: E402
from quote_ai.storage.product_catalog import DemoProductCatalog  # noqa: E402
from quote_ai.storage.repository import InMemoryQuoteRepository  # noqa: E402
from test_quote_service import LAKESIDE_EXTRACTED  # noqa: E402

ALICE = {"X-Forwarded-Email": "rep.alice@dbxlab.example"}
BOB = {"X-Forwarded-Email": "rep.bob@dbxlab.example"}
CALLS = []


def endpoint(records):
    """A stand-in for quote_ai_win_model_<target>: P(won) falls 0.2 per dollar above $7.00."""
    CALLS.append(records)
    return [[1 - p, p] for p in (round(0.76 - 0.2 * (r["unit_price"] - 7.0), 4) for r in records)]


def make(win_model=endpoint):
    ctx = Context(QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW), DemoProductCatalog(),
                  lambda text: Extracted.model_validate(LAKESIDE_EXTRACTED), win_model=win_model)
    return TestClient(create_app(ctx, serve_frontend=False))


def priced_lakeside(client):
    q = client.post("/api/quotes", json={"account": "Lakeside Regional Clinic", "text": fx.LAKESIDE_REQUEST},
                    headers=ALICE).json()
    q = client.post(f"/api/quotes/{q['quote_id']}/confirm", headers=ALICE,
                    json={"revision": q["revision"], "quantity_by_size": fx.LAKESIDE_SPLIT, "unit": "box",
                          "gloves_per_unit": 100}).json()
    return client.post(f"/api/quotes/{q['quote_id']}/quick-quote", json={"revision": q["revision"]},
                       headers=ALICE).json()


def test_lakeside_win_chance_at_three_prices_one_call_labelled_and_the_quote_unchanged():
    client = make()
    q = priced_lakeside(client)
    CALLS.clear()
    r = client.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": [8.20, 7.60, 7.90]}, headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(CALLS) == 1 and [rec["unit_price"] for rec in CALLS[0]] == [7.60, 7.90, 8.20]
    assert all(rec["account_id"] == "ACC-001" and rec["quantity_boxes"] == 400 for rec in CALLS[0])
    assert [(p["price"], p["win_chance"]) for p in body["prices"]] == [(7.60, 0.64), (7.90, 0.58), (8.20, 0.52)]
    assert body["consistent"] and body["label"] == "simulation, demo data" and "price book" in body["note"]
    assert body["model"].endswith("@champion")
    after = client.get(f"/api/quotes/{q['quote_id']}", headers=ALICE).json()
    assert after["revision"] == q["revision"] and after["total"] == q["total"] == 3440.0     # informs, never prices


def test_a_higher_price_that_scores_higher_is_flagged():
    client = make(lambda records: [0.5 + 0.1 * i for i, _ in enumerate(records)])
    q = priced_lakeside(client)
    body = client.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": [7.60, 8.20]},
                       headers=ALICE).json()
    assert body["consistent"] is False and body["problems"] == ["$8.20 scores 60%, above $7.60 at 50%"]


def test_refusals():
    client = make()
    q = priced_lakeside(client)
    assert client.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": [8.0]}).status_code == 401
    assert client.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": [8.0]},
                       headers=BOB).status_code == 403                    # another account's quote (AI-24)
    assert client.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": []},
                       headers=ALICE).status_code == 422
    assert client.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": [0]},
                       headers=ALICE).status_code == 422
    draft = client.post("/api/quotes", json={"account": "Lakeside Regional Clinic", "text": fx.LAKESIDE_REQUEST},
                        headers=ALICE).json()
    r = client.post("/api/win-chance", json={"quote_id": draft["quote_id"], "prices": [8.0]}, headers=ALICE)
    assert r.status_code == 409 and "shortlist" in r.json()["detail"]
    no_model = make(None)
    q = priced_lakeside(no_model)
    assert no_model.post("/api/win-chance", json={"quote_id": q["quote_id"], "prices": [8.0]},
                         headers=ALICE).status_code == 503
