"""The app's Season 5 routes through HTTP with no workspace: the streamed chat with source badges and proposals
(AI-18, AI-19), the conversation and customer memory (AI-20), the Pricing screen and the Approval inbox by role
(AI-21, QuoteAI v3)."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import lakeside_fixtures as fx  # noqa: E402
from quote_ai.agents.agent import QuoteAgent  # noqa: E402
from quote_ai.agents.standin import StandInClient, demo_win_model  # noqa: E402
from quote_ai.app.backend.context import season5, tool_deps  # noqa: E402
from quote_ai.app.backend.main import create_app  # noqa: E402
from quote_ai.gudid.api_client import DemoGudidApi, LiveGudid  # noqa: E402
from test_app_season4 import ALICE, BOB, confirmed, make_ctx  # noqa: E402

MARIA = {"X-Forwarded-Email": "mgr.maria@dbxlab.example"}
OMAR = {"X-Forwarded-Email": "mgr.omar@dbxlab.example"}
MANAGERS = {"mgr.maria@dbxlab.example", "mgr.omar@dbxlab.example"}


@pytest.fixture
def client():
    ctx = make_ctx(groups=lambda u: {"DBxLab_Sales_Managers"} if u in MANAGERS else set(),
                   inputs_as_of=lambda q: fx.fresh_inputs(), win_model=demo_win_model)
    ctx.service.clock = lambda: fx.NOW
    ctx.agent = QuoteAgent(deps=tool_deps(ctx, LiveGudid(DemoGudidApi(ctx.catalog)), live_first=True),
                           client=StandInClient(), endpoint="standin")
    return TestClient(create_app(season5(ctx), serve_frontend=False))


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(l.split(": ", 1) for l in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def shortlisted(client, who=ALICE):
    q = confirmed(client, who)
    return client.post(f"/api/shortlist/{q['quote_id']}", json={"revision": q["revision"]}, headers=who).json()["quote"]


def test_the_chat_streams_steps_sources_and_the_answer(client):
    q = confirmed(client)
    r = client.post(f"/api/chat/{q['quote_id']}", json={"text": "Find the options for this quote and price them."},
                    headers=ALICE)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events(r.text)
    kinds = [k for k, _ in ev]
    assert kinds[0] == "step" and kinds[-1] == "done" and "token" in kinds
    assert [d["label"] for k, d in ev if k == "step"][:3] == ["Searching candidates", "Checking eligibility",
                                                             "Checking fulfilment"]
    badges = {d["badge"] for k, d in ev if k == "source"}
    assert "DBxLab rules" in badges and "Win model (simulation, demo data)" in badges
    done = ev[-1][1]
    assert done["stop_reason"] == "answered" and done["violations"] == []
    assert done["facts"]["prices"]["GLOVE-A"]["unit_price"] == 8.60
    answer = "".join(d for k, d in ev if k == "token")
    assert "Options in order: 1. GLOVE-A, 2. GLOVE-D, 3. GLOVE-C" in answer


def test_the_live_record_badge_and_the_lakehouse_badge(client):
    q = confirmed(client)
    ev = events(client.post(f"/api/chat/{q['quote_id']}", json={"text": "Is Glove A's record current?"},
                            headers=ALICE).text)
    badges = [d["badge"] for k, d in ev if k == "source"]
    assert badges[0].startswith("Live GUDID record, retrieved today (version 3)")


def test_issue_now_is_a_proposal_card_and_nothing_changes(client):
    q = shortlisted(client)
    ev = events(client.post(f"/api/chat/{q['quote_id']}", json={"text": "Issue the quote now"}, headers=ALICE).text)
    proposal, = [d for k, d in ev if k == "proposal"]
    assert proposal["target"] == "ISSUED"
    after = client.get(f"/api/quotes/{q['quote_id']}", headers=ALICE).json()
    assert (after["state"], after["version"], after["revision"]) == (q["state"], q["version"], q["revision"])
    r = client.post(f"/api/chat/{q['quote_id']}/confirm", json={"target": "ISSUED", "revision": q["revision"]},
                    headers=ALICE)
    assert r.status_code == 422 and "no valid approval" in r.json()["detail"]   # QuoteService decides


def test_the_conversation_resumes_and_is_the_reps_own(client):
    q = confirmed(client)
    client.post(f"/api/chat/{q['quote_id']}", json={"text": "Find the options for this quote."}, headers=ALICE)
    r = client.get(f"/api/quotes/{q['quote_id']}/resume", headers=ALICE).json()
    assert [m["role"] for m in r["messages"]] == ["user", "assistant"] and r["quote"]["state"] == "REQUIREMENTS_CONFIRMED"
    ev = events(client.post(f"/api/chat/{q['quote_id']}", json={"text": "Size XL in the second option?"},
                            headers=ALICE).text)
    assert [d["name"] for k, d in ev if k == "step"] == ["check_fulfilment", "price_quote"]
    assert client.get(f"/api/quotes/{q['quote_id']}/resume", headers=BOB).status_code == 403          # AI-24
    assert [x["quote_id"] for x in client.get("/api/quotes?open=true", headers=BOB).json()] == []


def test_save_a_customer_fact_only_when_the_rep_confirms(client):
    q = confirmed(client)
    ev = events(client.post(f"/api/chat/{q['quote_id']}", json={"text": "Lakeside is always latex-free, save that."},
                            headers=ALICE).text)
    memory, = [d for k, d in ev if k == "memory"]
    assert memory["fact_key"] == "latex_free"
    assert client.get(f"/api/chat/{q['quote_id']}", headers=ALICE).json()["customer_facts"] == []
    r = client.post(f"/api/chat/{q['quote_id']}/memory", json={"fact_key": "latex_free", "fact_value": "true"},
                    headers=ALICE)
    assert r.status_code == 201 and r.json()["confirmed_by"] == "rep.alice@dbxlab.example"
    facts = client.get(f"/api/chat/{q['quote_id']}", headers=ALICE).json()["customer_facts"]
    assert [(f["fact_key"], f["confirmed_by"]) for f in facts] == [("latex_free", "rep.alice@dbxlab.example")]


def test_pricing_screen_to_issued_with_a_manager_approval(client):
    q = shortlisted(client)
    qid = q["quote_id"]
    assert client.get(f"/api/price/{qid}", headers=ALICE).status_code == 409       # no product chosen yet
    prev = client.get(f"/api/price/{qid}?product_id=GLOVE-A&asked=8.00", headers=ALICE).json()
    assert prev["lines"][0]["unit_price"] == 8.60 and prev["band"]["approval"] == "MANAGER"
    q = client.post(f"/api/price/{qid}", json={"revision": q["revision"], "product_id": "GLOVE-A",
                                               "asked_unit_price": 8.00}, headers=ALICE).json()
    assert q["state"] == "PRICED" and q["approval_needed"] == "MANAGER"
    assert client.post(f"/api/price/{qid}/submit", json={"revision": q["revision"]}, headers=ALICE).status_code == 428
    q = client.post(f"/api/price/{qid}/submit", json={"revision": q["revision"]},
                    headers=ALICE | {"Idempotency-Key": "submit-1"}).json()
    assert q["state"] == "PENDING_APPROVAL"
    v = q["version"]
    # the rep: no inbox, and approving their own quote is refused
    assert client.get("/api/approvals/me", headers=ALICE).json()["is_manager"] is False
    assert client.get("/api/approvals", headers=ALICE).status_code == 403
    r = client.post(f"/api/approvals/{qid}/v/{v}", headers=ALICE | {"Idempotency-Key": "x"})
    assert r.status_code == 403 and "no self-approval" in r.json()["detail"]
    # a manager sees the exact terms
    inbox = client.get("/api/approvals", headers=MARIA).json()
    item, = [i for i in inbox if i["quote_id"] == qid]
    assert item["version"] == v and item["discount_pct"] == 6.98 and item["lines"][0]["margin_pct"] == 23.08
    assert item["evidence_snapshot"] and len(item["terms_hash"]) == 64
    # a stale version is 409; the right one is approved, once
    assert client.post(f"/api/approvals/{qid}/v/{v - 1}", headers=MARIA | {"Idempotency-Key": "a0"}).status_code == 409
    a = client.post(f"/api/approvals/{qid}/v/{v}", headers=MARIA | {"Idempotency-Key": "a1"}).json()
    assert a["state"] == "APPROVED"
    assert client.post(f"/api/approvals/{qid}/v/{v}", headers=OMAR | {"Idempotency-Key": "a2"}).status_code == 409
    # issue clicked twice: one ISSUED, one event
    for _ in range(2):
        r = client.post(f"/api/price/{qid}/issue", json={"revision": a["revision"]},
                        headers=ALICE | {"Idempotency-Key": "issue-1"})
        assert r.status_code == 200 and r.json()["state"] == "ISSUED"
    trail = client.get(f"/api/quotes/{qid}", headers=ALICE).json()["events"]
    assert [e["action"] for e in trail].count("issue") == 1
    assert [(e["action"], e["actor"]) for e in trail][-3:] == [("submit", "rep.alice@dbxlab.example"),
                                                               ("approve", "mgr.maria@dbxlab.example"),
                                                               ("issue", "rep.alice@dbxlab.example")]


def test_a_later_date_voids_the_approval_and_20_percent_is_refused(client):
    q = shortlisted(client)
    qid = q["quote_id"]
    q = client.post(f"/api/price/{qid}", json={"revision": q["revision"], "product_id": "GLOVE-A",
                                               "asked_unit_price": 8.00}, headers=ALICE).json()
    q = client.post(f"/api/price/{qid}/submit", json={"revision": q["revision"]},
                    headers=ALICE | {"Idempotency-Key": "s"}).json()
    q = client.post(f"/api/approvals/{qid}/v/{q['version']}", headers=MARIA | {"Idempotency-Key": "a"}).json()
    r3 = client.post(f"/api/price/{qid}/revise", json={"revision": q["revision"], "delivery_days": 21,
                                                       "product_id": "GLOVE-C"}, headers=ALICE).json()
    assert (r3["state"], r3["version"], r3["requirements"]["version"], r3["product_id"]) == (
        "PENDING_APPROVAL", q["version"] + 1, q["requirements"]["version"] + 1, "GLOVE-C")
    assert r3["approval"] is None
    band = client.get(f"/api/price/{qid}?asked=6.88", headers=ALICE).json()["band"]
    assert band["approval"] == "REFUSED" and band["can_request_approval"] is False
    r = client.post(f"/api/price/{qid}/revise", json={"revision": r3["revision"], "asked_unit_price": 6.88},
                    headers=ALICE)
    assert r.status_code == 422 and "refused by the pricing policy" in r.json()["detail"]
