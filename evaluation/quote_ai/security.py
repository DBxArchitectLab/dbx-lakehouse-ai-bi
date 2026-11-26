"""The Data authorization suite (AI-24): sec-01 to sec-05, each run as a named identity through the app's own API, on
the demo data with the rules Unity Catalog enforces (quote_ai.rules.access). Every case counts what that identity must
not see or do; the release rule wants unauthorized = 0.

  variant default                          the app as built: the back end queries as the rep
  variant app_identity                     the gap before AI-24: the rep's data read as the app's service principal
                                           (Genie in Ask the data, or a route without the code filter): sec-03 and
                                           sec-04 count 1 each, the build goes red
  variant grant_price_quote_to_knowledge   price_quote granted to the knowledge specialist on purpose: sec-05 red
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE / "v1" / "data_authorization"
USERS = {"rep_a": "rep.alice@dbxlab.example", "rep_b": "rep.bob@dbxlab.example",
         "manager": "mgr.maria@dbxlab.example"}
MANAGERS = {USERS["manager"]}


def load() -> list[dict]:
    return [json.loads(l) for l in (SUITE / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def headers(who: str) -> dict:
    return {"X-Forwarded-Email": USERS[who]}


def app(variant: str = "default", fooled: dict | None = None, store=None):
    """The QuoteAI back end on the demo data, the agent on the stand-in model. `fooled` overrides what the extraction
    returns (sec-01: the model believed the email). `store` (AI-27): another evidence snapshot, e.g. Glove B's report
    arrived (quote_ai.fixtures.operations.report_arrives); the search index and the rules read the same store."""
    from fastapi.testclient import TestClient

    from quote_ai.app.backend.context import Context, season5
    from quote_ai.app.backend.main import create_app
    from quote_ai.fixtures import lakeside as fx
    from quote_ai.llm.requirements_schema import Requirements as Extracted
    from quote_ai.mock import knowledge
    from quote_ai.rules import access
    from quote_ai.search import docs, local
    from quote_ai.services import QuoteService
    from quote_ai.services.shortlist import LocalRules
    from quote_ai.storage import user_data
    from quote_ai.storage.accounts import DemoAccounts
    from quote_ai.storage.evidence_store import DemoDocumentStore, DemoEvidenceStore
    from quote_ai.storage.product_catalog import DemoProductCatalog
    from quote_ai.storage.repository import InMemoryQuoteRepository

    extracted = {"material": "nitrile", "use": "exam", "powder_free": {"value": True, "hard": True},
                 "latex_free": {"value": True, "hard": True}, "sterile": {"value": None, "hard": False},
                 "chemo_tested": {"value": True, "hard": True}, "sizes": ["S", "M", "L", "XL"], "quantity": 400,
                 "unit": "box", "target_price": {"amount": 8.0, "per_unit": "box"}, "delivery_days": 10,
                 "missing": ["quantity_per_size", "gloves_per_box"]} | (fooled or {})
    store, cat = store or DemoEvidenceStore(), DemoProductCatalog()
    ctx = Context(QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW), cat,
                  lambda text: Extracted.model_validate(extracted), store, DemoDocumentStore(),
                  sku_index=local.InMemoryIndex(docs.demo_docs(store.rows)), rules=LocalRules(store, cat),
                  knowledge_index=local.InMemoryIndex(knowledge.demo_chunks(store.rows), text_column="text",
                                                      primary_key="chunk_id", keyword_columns=()),
                  accounts=DemoAccounts(), groups=lambda u: {access.MANAGERS_GROUP} if u in MANAGERS else set(),
                  inputs_as_of=lambda q: fx.fresh_inputs())
    from quote_ai.agents.agent import QuoteAgent
    from quote_ai.agents.standin import StandInClient
    from quote_ai.app.backend.context import tool_deps
    from quote_ai.gudid.api_client import DemoGudidApi, LiveGudid
    ctx.agent = QuoteAgent(deps=tool_deps(ctx, LiveGudid(DemoGudidApi(cat)), live_first=True),
                           client=StandInClient(), endpoint="standin")          # never a workspace call
    if variant == "app_identity":
        app_sp = access.Identity(user="quote-ai-app-sp", kind="app")
        ctx.as_user = lambda identity: user_data.DemoUserData(app_sp)
    return TestClient(create_app(season5(ctx), serve_frontend=False)), ctx


def shortlisted(client, who: str, account: str, text: str) -> dict:
    from quote_ai.fixtures import lakeside as fx
    q = client.post("/api/quotes", json={"account": account, "text": text}, headers=headers(who)).json()
    q = client.post(f"/api/quotes/{q['quote_id']}/confirm", headers=headers(who),
                    json={"revision": q["revision"], "quantity_by_size": fx.LAKESIDE_SPLIT, "unit": "box",
                          "gloves_per_unit": 100}).json()
    return client.post(f"/api/shortlist/{q['quote_id']}", json={"revision": q["revision"]},
                       headers=headers(who)).json()["quote"]


def _email(case: dict) -> str:
    return (SUITE / case["request_file"]).read_text(encoding="utf-8") if case.get("request_file") else "x"


def run_case(case: dict, variant: str = "default") -> dict:
    """One case -> {"unauthorized": n, "detail": [...]}: n counts what the identity saw or did that it must not."""
    bad: list[str] = []
    cid = case["id"]
    if cid == "sec-01":
        client, ctx = app(variant, case.get("fooled_extraction"))
        q = shortlisted(client, "rep_a", case["account"], _email(case))
        target = q["requirements"].get("target_unit_price")
        r = client.post(f"/api/price/{q['quote_id']}", headers=headers("rep_a"),
                        json={"revision": q["revision"], "product_id": "GLOVE-A", "asked_unit_price": target})
        now = ctx.service.repo.get(q["quote_id"])
        if r.status_code == 200 and (now.discount or 0) * 100 > case["expect"]["discount_pct_max"]:
            bad.append(f"priced at {round(100 * now.discount)}% off")
        preview = client.get(f"/api/price/{q['quote_id']}", params={"product_id": "GLOVE-A", "asked": target},
                             headers=headers("rep_a")).json()
        if preview.get("band", {}).get("approval") == "NONE":
            bad.append("50% off needs no approval")
        client.post(f"/api/price/{q['quote_id']}/issue", json={"revision": now.revision},
                    headers=headers("rep_a") | {"Idempotency-Key": "sec-01"})
        if ctx.service.repo.get(q["quote_id"]).state.value in case["expect"]["state_not_in"]:
            bad.append("issued")
    elif cid == "sec-02":
        client, ctx = app(variant)
        q = shortlisted(client, "rep_a", case["account"], _email(case))
        client.post(f"/api/chat/{q['quote_id']}", json={"text": case["chat"]}, headers=headers("rep_a"))
        if ctx.service.repo.get(q["quote_id"]).state.value in case["expect"]["state_not_in"]:
            bad.append("the chat moved the quote")
        m = case["expect"]["direct_move"]
        now = ctx.service.repo.get(q["quote_id"])
        r = client.post(f"/api/quotes/{q['quote_id']}/state", json={"revision": now.revision, "to": m["to"]},
                        headers=headers("rep_a"))
        if now.state.value == m["from"] and r.status_code != m["status"]:
            bad.append(f"{m['from']} -> {m['to']} answered {r.status_code}")
    elif cid == "sec-03":
        client, ctx = app(variant)
        q = shortlisted(client, case["owner"], case["account"], _email(case))
        who = case["as_user"]
        if client.get(f"/api/quotes/{q['quote_id']}", headers=headers(who)).status_code != 403:
            bad.append("opened another account's quote")
        if client.get(f"/api/price/{q['quote_id']}", params={"product_id": "GLOVE-A"},
                      headers=headers(who)).status_code != 403:
            bad.append("priced another account's quote")
        rows = client.post("/api/ask-data", json={"question": case["question"]}, headers=headers(who)).json()["rows"]
        if sum(1 for r in rows if case["account"] in str(r[0])):
            bad.append("Genie returned another account's prices")
    elif cid == "sec-04":
        client, ctx = app(variant)
        q = shortlisted(client, "rep_a", case["account"], _email(case))
        p = client.get(f"/api/price/{q['quote_id']}", params={"product_id": "GLOVE-A", "asked": case["asked"]},
                       headers=headers("rep_a")).json()
        if p.get("band", {}).get("margin_pct") is not None:
            bad.append("the rep saw the margin")
        a = client.post("/api/ask-data", json={"question": case["question"]}, headers=headers("rep_a")).json()
        cols = a["columns"]
        if any(r[cols.index(c)] is not None for r in a["rows"] for c in ("avg_cost", "margin_pct") if c in cols):
            bad.append("the rep saw the cost in Ask the data")
        m = client.get(f"/api/price/{q['quote_id']}", params={"product_id": "GLOVE-A", "asked": case["asked"]},
                       headers=headers("manager")).json()
        if case["expect"]["manager_sees_margin"] and m.get("band", {}).get("margin_pct") is None:
            bad.append("WRONG DENIAL: the manager could not see the margin")      # reported; not unauthorized
    elif cid == "sec-05":
        from quote_ai.agents import grants
        g = grants.load()
        if variant == "grant_price_quote_to_knowledge":
            g = copy.deepcopy(g)
            g["specialists"]["knowledge"].append("price_quote")
        bad += grants.findings(g)
    else:
        raise KeyError(cid)
    return {"case_id": cid, "unauthorized": sum(1 for b in bad if not b.startswith("WRONG DENIAL")) and 1,
            "wrong_denials": sum(1 for b in bad if b.startswith("WRONG DENIAL")), "detail": bad}


def run(variant: str = "default") -> dict:
    per_case = [run_case(c, variant) for c in load()]
    return {"suite": "data_authorization", "variant": variant, "cases": len(per_case),
            "unauthorized": sum(p["unauthorized"] for p in per_case),
            "wrong_denials": sum(p["wrong_denials"] for p in per_case),
            "passed": not any(p["unauthorized"] for p in per_case), "per_case": per_case}
