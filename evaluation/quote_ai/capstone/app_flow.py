"""The whole Lakeside transaction through the app's own API (AI-27): the capstone's "App" criterion and the end-to-end
test. No notebook, no direct call into a service: every step is a route a React screen calls.

  build()        the back end on the demo data with Glove B's report arrived (snapshot product_evidence@v57), the
                 agent on the stand-in model, the demo win model, the demo Ops page: no workspace call
  run(client)    request -> r1 (what is missing) -> confirm r2 -> shortlist -> compare + ask -> Ask the data ->
                 price Glove A at $8.00 -> win chance at three prices (it needs the quote's SKUs) -> submit -> the
                 manager approves that version -> issue; then the hood.
                 Returns every step's facts, the checks that failed (none expected) and the seconds it took.

Reuses evaluation/quote_ai/security.app (the same demo back end the Data authorization suite drives).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
REP, MANAGER = "rep_a", "manager"
ACCOUNT = "Lakeside Regional Clinic"


def build():
    if str(EVAL) not in sys.path:
        sys.path.insert(0, str(EVAL))
    import security
    from quote_ai.agents.standin import demo_win_model
    from quote_ai.app.backend.context import _demo_ops
    from quote_ai.fixtures import operations
    client, ctx = security.app(store=operations.report_arrives())
    ctx.win_model = demo_win_model
    ctx.ops = _demo_ops()
    return client, ctx


def run(client=None) -> dict:
    if str(EVAL) not in sys.path:
        sys.path.insert(0, str(EVAL))
    from security import headers
    from quote_ai.fixtures import lakeside as fx
    if client is None:
        client, _ = build()
    rep, mgr = headers(REP), headers(MANAGER)
    failed: list[str] = []
    steps: list[dict] = []

    def check(ok: bool, what: str) -> None:
        if not ok:
            failed.append(what)

    def call(method: str, path: str, who: dict, step: str, **kw):
        r = getattr(client, method)(path, headers=who | kw.pop("extra", {}), **kw)
        steps.append({"step": step, "route": f"{method.upper()} {path.split('?')[0]}", "status": r.status_code})
        check(r.status_code in (200, 201), f"{step}: HTTP {r.status_code}")
        return r.json()

    start = time.perf_counter()
    q = call("post", "/api/quotes", rep, "1 request", json={"account": ACCOUNT, "text": fx.LAKESIDE_REQUEST})
    qid = q["quote_id"]
    check(q["state"] == "DRAFT" and q["requirements"]["version"] == 1, "r1 is a DRAFT")
    r1_missing = q["requirements"].get("missing", [])
    check(any("size" in m for m in r1_missing) and len(r1_missing) >= 2, "r1 says what is missing")

    q = call("post", f"/api/quotes/{qid}/confirm", rep, "2-3 confirm r2",
             json={"revision": q["revision"], "quantity_by_size": fx.LAKESIDE_SPLIT, "unit": "box",
                   "gloves_per_unit": 100})
    check(q["state"] == "REQUIREMENTS_CONFIRMED" and q["requirements"]["version"] == 2, "r2 confirmed")
    check(sorted((l["size"], l["quantity"]) for l in q["lines"]) == sorted(fx.LAKESIDE_SPLIT.items()),
          "one line per size: S 60, M 140, L 140, XL 60")

    body = call("post", f"/api/shortlist/{qid}", rep, "4-6 shortlist", json={"revision": q["revision"]})
    q, s = body["quote"], body["shortlist"]
    eligible = {e["product_id"]: e for e in s["eligible"]}
    excluded = {e["product_id"]: e for e in s["excluded"]}
    check(q["state"] == "SHORTLISTED", "SHORTLISTED")
    check({"GLOVE-A", "GLOVE-B", "GLOVE-C", "GLOVE-D"} <= set(eligible), "A, B, C and D eligible")
    check("GLOVE-E" in excluded and any(c["requirement"] == "latex_free" and c["result"] == "verified_mismatch"
                                        for c in excluded["GLOVE-E"]["checks"]), "E excluded for latex")
    check(all(c["result"] == "verified_match" for e in eligible.values() for c in e["checks"]),
          "every eligible check verified (unknown never a match)")
    ful = {p: (e.get("fulfilment") or {}) for p, e in eligible.items()}
    check(ful.get("GLOVE-A", {}).get("status") == "full", "A ships in full")
    check(ful.get("GLOVE-D", {}).get("status") == "full", "D ships in full")
    check(ful.get("GLOVE-C", {}).get("label") == "alternative, needs acceptance", "C is an alternative")

    table = call("get", f"/api/compare/{qid}", rep, "compare")
    ask = call("post", f"/api/compare/{qid}/ask", rep, "ask about this product",
               json={"question": "What supports the chemo claim?", "product_id": "GLOVE-A"})
    check(any(st["layer"] == "business_truth" and "RPT-CHEMO-A" in st["citation"] for st in ask["statements"]),
          "the chemo claim cites the supplier document")
    data = call("post", "/api/ask-data", rep, "ask the data", json={"question": "What did Lakeside pay last year?"})

    preview = call("get", f"/api/price/{qid}?product_id=GLOVE-A&asked=8.00", rep, "pricing preview")
    check(preview["lines"][0]["unit_price"] == 8.60 and preview["band"]["approval"] == "MANAGER",
          "Glove A at $8.60 list; $8.00 needs a manager")
    q = call("post", f"/api/price/{qid}", rep, "7 price",
             json={"revision": q["revision"], "product_id": "GLOVE-A", "asked_unit_price": 8.00})
    check(q["state"] == "PRICED" and q["approval_needed"] == "MANAGER", "PRICED, approval needed")
    # the win chance needs the quote's SKUs, which the chosen glove gives: read-only, nothing on the quote changes
    win = call("post", "/api/win-chance", rep, "win chance", json={"quote_id": qid, "prices": [7.60, 8.00, 8.60]})
    check(len(win.get("prices", [])) == 3 and "simulation" in win.get("label", "").lower(),
          "win chance at three prices, labelled a simulation")
    q = call("post", f"/api/price/{qid}/submit", rep, "8 submit", json={"revision": q["revision"]},
             extra={"Idempotency-Key": f"{qid}-submit"})
    check(q["state"] == "PENDING_APPROVAL", "PENDING_APPROVAL")
    version = q["version"]
    inbox = call("get", "/api/approvals", mgr, "approval inbox")
    item = next((i for i in inbox if i["quote_id"] == qid), None)
    check(item is not None and item["version"] == version and round(item["discount_pct"], 1) == 7.0,
          "the manager sees this version, 7% off")
    a = call("post", f"/api/approvals/{qid}/v/{version}", mgr, "approve", extra={"Idempotency-Key": f"{qid}-approve"})
    check(a["state"] == "APPROVED", "APPROVED")
    q = call("post", f"/api/price/{qid}/issue", rep, "9 issue", json={"revision": a["revision"]},
             extra={"Idempotency-Key": f"{qid}-issue"})
    check(q["state"] == "ISSUED", "ISSUED")
    seconds = time.perf_counter() - start

    hood = call("get", f"/api/quotes/{qid}", rep, "open the hood")
    events = hood["events"]
    check(hood["requirements"]["version"] == 2 and hood["evidence_snapshot"] and hood["inventory_snapshot"],
          "the version pins r2 and the evidence and inventory snapshots")
    check(all(l["sku_id"].startswith("GLOVE-A") and l["unit_price"] == 8.00 for l in hood["lines"]),
          "issued with the Glove A lines at $8.00")
    options = {c["product_id"]: c["status"] for c in hood.get("candidates", [])}
    check({"GLOVE-B", "GLOVE-D"} <= {p for p, st in options.items() if st == "eligible"},
          "B and D listed as eligible options")
    check(all(e.get("actor") for e in events) and [e["action"] for e in events].count("issue") == 1,
          "quote_event: every step with its actor, one issue")
    check(("approve", "mgr.maria@dbxlab.example") in [(e["action"], e["actor"]) for e in events],
          "the approval of that version is in the history")
    ops = call("get", f"/api/ops?quote_id={qid}", mgr, "ops page")
    return {"quote_id": qid, "state": q["state"], "seconds": round(seconds, 3), "steps": steps, "failed": failed,
            "passed": not failed, "eligible": sorted(eligible), "excluded": sorted(excluded),
            "fulfilment": {p: f.get("status") for p, f in ful.items()}, "events": [e["action"] for e in events],
            "compare_columns": [c["product_id"] for c in table.get("columns", [])],
            "ask_data_rows": len(data.get("rows", [])), "win_chance": win.get("prices"), "ops_status": ops["status"]}
