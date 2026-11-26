"""The app's Season 6 routes through HTTP with no workspace: feedback on every answer (AI-22)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from quote_ai.app.backend.feedback import DemoFeedback, MlflowFeedback  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from test_app_season4 import ALICE, make_ctx  # noqa: E402


@pytest.fixture
def ctx():
    return make_ctx(feedback=DemoFeedback())


@pytest.fixture
def client(ctx):
    return TestClient(create_app(ctx, serve_frontend=False))


def test_thumbs_up_is_saved_on_the_trace_as_the_signed_in_rep(client, ctx):
    r = client.post("/api/feedback", json={"trace_id": "tr-1", "thumbs_up": True}, headers=ALICE)
    assert r.status_code == 200 and r.json() == {"saved": True, "on_trace": True, "candidate_id": None}
    (rating,) = ctx.feedback.ratings
    assert rating == {"trace_id": "tr-1", "name": "rep_rating", "value": True, "rationale": None,
                      "source": {"source_type": "HUMAN", "source_id": "rep.alice@dbxlab.example"}}
    assert ctx.feedback.candidates == []


def test_thumbs_down_becomes_a_candidate_case_not_a_test(client, ctx):
    body = {"trace_id": "tr-9", "thumbs_up": False, "comment": "Missed the shipping cost of Glove C",
            "quote_id": "Q-1", "surface": "compare"}
    r = client.post("/api/feedback", json=body, headers=ALICE).json()
    assert r["candidate_id"].startswith("cand-")
    (c,) = ctx.feedback.candidates
    assert (c["trace_id"], c["rep"], c["status"], c["surface"]) == ("tr-9", "rep.alice@dbxlab.example", "candidate",
                                                                    "compare")
    assert ctx.feedback.ratings[0]["value"] is False


def test_the_rep_comes_from_the_header_never_the_body(client):
    assert client.post("/api/feedback", json={"trace_id": "t", "thumbs_up": True}).status_code == 401
    r = client.post("/api/feedback", json={"trace_id": "t", "thumbs_up": True, "rep": "mgr.maria"}, headers=ALICE)
    assert r.status_code == 200


def test_a_thumbs_down_with_nothing_to_go_on_is_refused(client):
    r = client.post("/api/feedback", json={"thumbs_up": False}, headers=ALICE)
    assert r.status_code == 422


def test_the_workspace_store_writes_the_candidate_row(monkeypatch):
    from quote_ai.app.backend import feedback
    logged, sent = [], []
    monkeypatch.setattr(feedback, "log_on_trace", lambda *a: logged.append(a))

    class Sql:
        def execute(self, sql, params=None):
            sent.append((sql, params))
            return 1
    f = MlflowFeedback(Sql(), "cat.quote_ai_eval")
    out = f.rate("tr-2", "rep.bob@dbxlab.example", False, "wrong", "Q-2", "chat")
    assert logged == [("tr-2", "rep.bob@dbxlab.example", False, "wrong")]
    (sql, params), = sent
    assert sql.startswith("INSERT INTO cat.quote_ai_eval.candidate_cases") and params["candidate_id"] == \
        out["candidate_id"]
