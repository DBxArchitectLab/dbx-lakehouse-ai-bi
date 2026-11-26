"""The app's Season 7 routes through HTTP with no workspace: the Ops page for managers (AI-26), and the Compare
screen's fallback when the comparison model fails."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.app.backend.context import _demo_ops  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.app.backend.routers.compare import NO_PROSE  # noqa: E402
from test_app_season4 import ALICE, confirmed, make_ctx  # noqa: E402

MARIA = {"X-Forwarded-Email": "mgr.maria@dbxlab.example"}


def app(**change):
    ctx = make_ctx(groups=lambda u: {"DBxLab_Sales_Managers"} if u == MARIA["X-Forwarded-Email"] else set(),
                   inputs_as_of=lambda q: fx.fresh_inputs())
    ctx.ops = _demo_ops()                       # what season5() wires in the local demo (QUOTE_AI_DEMO=1)
    for k, v in change.items():
        setattr(ctx, k, v)
    return TestClient(create_app(ctx, serve_frontend=False))


def test_the_ops_page_is_for_managers_only():
    c = app()
    assert c.get("/api/ops", headers=ALICE).status_code == 403
    assert c.get("/api/ops").status_code == 401
    v = c.get("/api/ops", headers=MARIA).json()
    assert v["status"] == "green" and v["label"].startswith("Demo data")
    assert {f["name"] for f in v["freshness"]} >= {"gudid_daily_updates", "supplier_documents", "inventory"}
    assert len(v["slo"]) == 7 and "app" in v["cost"]["by_step_usd"]


def test_one_quotes_cost_and_a_bad_quote_id_is_refused():
    c = app()
    assert "quote_cost" in c.get("/api/ops?quote_id=Q-demo-1113-0", headers=MARIA).json()
    assert c.get("/api/ops?quote_id=x;drop", headers=MARIA).status_code == 422


def test_the_manager_menu_shows_ops():
    assert app().get("/api/approvals/me", headers=MARIA).json()["is_manager"] is True


def _shortlisted(c):
    q = confirmed(c)
    return c.post(f"/api/shortlist/{q['quote_id']}", json={"revision": q["revision"]}, headers=ALICE).json()["quote"]


def test_the_comparison_model_fails_and_compare_falls_back_to_the_table(caplog):
    def down(prompt):
        raise TimeoutError("quote_ai_chat_dev: 504")
    c = app(generate=down)
    q = _shortlisted(c)
    url = f"/api/compare/{q['quote_id']}/ask"
    assert c.get(f"/api/compare/{q['quote_id']}", headers=ALICE).status_code == 200     # the table needs no model
    with caplog.at_level("ERROR", logger="quote_ai.app.compare"):
        r = c.post(url, json={"question": "Can we offer it for chemo use?", "product_id": "GLOVE-B"},
                   headers=ALICE).json()
    assert r["fallback"] == NO_PROSE
    assert any("no chemo test report on file" in s["text"] for s in r["statements"])   # the rules' finding stays
    assert "comparison model call failed" in caplog.text                                 # the app log shows it
    again = c.post(url, json={"question": "Can we offer it for chemo use?", "product_id": "GLOVE-B"},
                   headers=ALICE).json()
    assert again["cached"] is False                                                      # a fallback is never cached
