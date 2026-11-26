"""The Season 5 suites (AI-18 to AI-21): Pricing, Reliability and Agent workflow. Deterministic where the thing measured
is deterministic; the workflow suite runs the agent on the stand-in model locally (CI) and on the chat model service
in the workspace (--endpoint).

  pricing      price_quote on fixed books and packaging (units, tiers, margin floor, effective dates) and the
               discount bands (4%, 7%, 16%, below the floor): zero tolerance, in CI next to the eligibility suite
  reliability  each case a scenario run against the real code with fakes for the slow or failing parts: timeouts
               and retries, a rule answer never retried, no progress, duplicates served once, a tool down named in
               the answer, the GUDID fallback labelled, stale data never issues, concurrent approvals, double issue:
               zero tolerance
  workflow     tool choice, the boundary (no state change, no write, proposals only), resume, fallback, and 100
               end-to-end requests (kind e2e: AI-21's experiment). Metrics: tool_choice_accuracy, task_success,
               groundedness; gates: rule_fidelity_violations, state_changes, unsourced_results
               --variant overlapping adds AI-19's find_gloves tool (the break); always_playbook puts the quoting
               playbook in every prompt instead of loading the skill on demand (compare tokens_per_request);
               --design one_agent | specialists
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))


def load(suite: str) -> list[dict]:
    path = HERE / "v1" / suite / "cases.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# ------------------------------------------------------------------ pricing

BOOKS = {
    "standard": [
        {"id": "PB-A-M-1", "sku_id": "GLOVE-A-M", "tier": "standard", "min_units": 1, "list_price": 8.60,
         "unit_price": 8.60, "cost": 6.50, "min_margin": 0.12},
        {"id": "PB-A-M-250", "sku_id": "GLOVE-A-M", "tier": "standard", "min_units": 250, "list_price": 8.60,
         "unit_price": 8.30, "cost": 6.50, "min_margin": 0.12},
        {"id": "PB-A-M-C", "sku_id": "GLOVE-A-M", "tier": "contract", "min_units": 1, "list_price": 8.60,
         "unit_price": 8.17, "cost": 6.50, "min_margin": 0.12},
        {"id": "PB-A-M-LOW", "sku_id": "GLOVE-A-M", "tier": "clearance", "min_units": 1, "list_price": 8.60,
         "unit_price": 7.00, "cost": 6.50, "min_margin": 0.12},
        {"id": "PB-A-M-OLD", "sku_id": "GLOVE-A-M", "tier": "dated", "min_units": 1, "list_price": 8.40,
         "unit_price": 8.40, "cost": 6.50, "min_margin": 0.12, "valid_to": "2026-10-31"},
        {"id": "PB-A-M-NEW", "sku_id": "GLOVE-A-M", "tier": "dated", "min_units": 1, "list_price": 8.60,
         "unit_price": 8.60, "cost": 6.50, "min_margin": 0.12, "valid_from": "2026-11-01"},
    ]}
PACKAGING = {"GLOVE-A-M": {"selling_unit": "box", "gloves_per": {"each": 1, "box": 100, "case": 1000}}}


def predict_pricing(case: dict) -> dict:
    from quote_ai.rules import pricing
    from quote_ai.rules.policy import Policy
    if case["kind"] == "band":
        row = pricing.PriceBookRow(id="X", sku_id="X", tier="standard", min_units=1, list_price=case["list_price"],
                                   unit_price=case["list_price"], cost=case["cost"], min_margin=case["min_margin"])
        return {"approval": pricing.approval_required(case["list_price"], case["unit_price"], row,
                                                      Policy().pricing)}
    book = [pricing.PriceBookRow.model_validate(r) for r in BOOKS[case.get("book", "standard")]]
    pack = {k: pricing.Packaging.model_validate(v) for k, v in PACKAGING.items()}
    try:
        out = pricing.price_quote(case["sku_id"], case["qty"], case["unit"], case["tier"], book, pack,
                                  on=date.fromisoformat(case["on"]) if case.get("on") else None)
        return {k: out[k] for k in ("boxes", "unit_price", "total", "price_book_id")}
    except pricing.PricingError as e:
        return {"error": str(e)}


def pricing_failures(outputs: dict, expected: dict) -> int:
    """Zero tolerance: every expected field equal; an expected error must be raised with that text."""
    if "error" in expected:
        return int(expected["error"] not in outputs.get("error", ""))
    return sum(outputs.get(k) != v for k, v in expected.items())


# ------------------------------------------------------------------ reliability: scenarios against the real code

def _no_wait():
    return {"sleep": lambda s: None, "timeout": 0.05}


def scenario(name: str) -> dict:
    from quote_ai.fixtures import agents as af
    from quote_ai.fixtures import lakeside as fx
    from quote_ai.agents import reliability as r
    from quote_ai.agents.loop_by_hand import run
    if name == "timeout_then_clear_stop":                     # AI-18
        _, q = af.confirmed()
        box = af.toolbox(q)
        base = box.run
        box.run = lambda n, a: (time.sleep(0.1), base(n, a))[1] if n == "get_device" else base(n, a)
        out = run([af.user("Show Glove A's GUDID record")], box,
                  af.ScriptedClient([[("get_device", {"product_id": "GLOVE-A"})], "ok"]), "t", partial=False,
                  retry=_no_wait())
        return {"stop_reason": out.stop_reason, "attempts": len(next(e for e in out.events
                                                                       if e["type"] == "tool_result")
                                                                  ["output"]["attempts"]),
                "clear_stop": out.answer.startswith("Stopped: the device record could not be reached")}
    if name == "pricing_error_not_retried":                   # AI-18
        _, q = af.confirmed()
        calls = []
        box = af.toolbox(q)
        out = r.call_tool("price_quote", {"product_id": "GLOVE-F"}, lambda n, a: (calls.append(n), box.run(n, a))[1],
                          sleep=lambda s: None)
        return {"refused": "refused" in out, "calls": len(calls)}
    if name == "same_call_twice_stops":                       # AI-18
        _, q = af.confirmed()
        call = [("check_eligibility", {"product_ids": ["GLOVE-A"]})]
        out = run([af.user("Is A eligible?")], af.toolbox(q), af.ScriptedClient([call] * 4), "t")
        return {"stop_reason": out.stop_reason, "tool_calls": len(out.tools_called)}
    if name == "step_limit":                                  # AI-18
        _, q = af.confirmed()
        turns = [[("get_evidence", {"product_id": f"GLOVE-{c}"})] for c in "ABCDEFGHIJ"]
        out = run([af.user("everything")], af.toolbox(q), af.ScriptedClient(turns), "t", max_steps=8)
        return {"stop_reason": out.stop_reason, "tool_calls": len(out.tools_called)}
    if name == "duplicate_lookup_one_call":                   # AI-19
        _, q = af.confirmed()
        box = af.toolbox(q)
        di = box.run("get_device", {"product_id": "GLOVE-A"})["primary_di"]
        turn = r.Turn()
        turn.call("gudid_live_lookup", {"di": di}, box.run)
        _, how = turn.call("gudid_live_lookup", {"di": di}, box.run)
        return {"api_calls": box.deps.live_gudid.calls, "second": how}
    if name == "genie_down_partial_answer":                   # AI-19
        def paused(question):
            raise RuntimeError("paused")
        _, q = af.confirmed()
        ev = list(af.agent(genie=paused).events(
            [af.user("What has Lakeside bought in the last year, and is Glove A current?")], q, "rep.alice",
            retry=af.NO_WAIT))
        ans = ev[-1]["answer"]
        return {"names_missing": "Buying history is unavailable right now" in ans,
                "invented_prices": "a box on average" in ans, "genie_calls": sum(
                    1 for e in ev if e["type"] == "tool_result" and e["name"] == "ask_genie"),
                "still_answers_glove_a": "Glove A" in ans}
    if name == "permission_denied_not_retried":               # AI-19
        calls = []

        def denied(n, a):
            calls.append(n)
            raise r.ToolRefused("PERMISSION_DENIED")
        out, how = r.Turn().call("read_price_book_cost", {}, denied, sleep=lambda s: None)
        return {"served": how, "calls": len(calls), "error": out.get("error")}
    if name == "proposal_is_idempotent":                      # AI-19
        _, q = af.priced()
        box = af.toolbox(q)
        a = box.run("propose_transition", {"target": "ISSUED", "reason": "a"})["proposal"]["idempotency_key"]
        b = box.run("propose_transition", {"target": "ISSUED", "reason": "b"})["proposal"]["idempotency_key"]
        return {"same_key": a == b}
    if name == "gudid_down_fallback_labelled":                # AI-20
        _, q = af.confirmed()
        box = af.toolbox(q, live_first=True)
        box.deps.live_gudid.fetch.down = True
        d = box.run("get_device", {"product_id": "GLOVE-A"})
        return {"source": d["source"], "as_of": d.get("as_of"), "labelled": "fallback" in d}
    if name == "stale_gudid_blocks_issue":                    # AI-20
        from quote_ai.services.quote_service import StaleEvidence
        svc, q = af.priced(asked=8.40)
        q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
        try:
            svc.issue(q.quote_id, q.revision, "rep.alice", key="i",
                      inputs_as_of=fx.fresh_inputs() | {"gudid": fx.NOW - timedelta(days=2, hours=1)})
            return {"refused": False}
        except StaleEvidence as e:
            return {"refused": True, "names": e.stale[0].split(" as of")[0],
                    "state": svc.repo.get(q.quote_id).state.value}
    if name == "fresh_inputs_issue":                          # AI-20
        svc, q = af.priced(asked=8.40)
        q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
        return {"state": svc.issue(q.quote_id, q.revision, "rep.alice", key="i",
                                   inputs_as_of=fx.fresh_inputs()).state.value}
    if name == "migration_mismatch_stops":                    # AI-20
        from quote_ai.ops import migrate_quotes_to_lakebase as mig
        from quote_ai.storage.lakebase import sqlite_db
        from quote_ai.fixtures.agents import delta_with_quotes
        sql, db = delta_with_quotes(), sqlite_db()
        original = mig._copy
        mig._copy = lambda tx, t, rows: original(tx, t, rows[:-1] if t == "quote_event" else rows)
        try:
            mig.copy_and_prove(lambda t: sql.query(f"SELECT * FROM {t}"), db)
            stopped = False
        except mig.MigrationMismatch:
            stopped = True
        finally:
            mig._copy = original
        with db.tx() as tx:
            left = tx.one("SELECT count(*) AS n FROM quote")["n"]
        return {"stopped": stopped, "rows_left": left}
    if name in ("concurrent_approve_one_wins", "double_issue_one_event", "stale_version_409"):   # AI-21
        import threading
        from quote_ai.storage.lakebase import sqlite_db
        from quote_ai.storage.lakebase_repository import LakebaseQuoteRepository
        from quote_ai.storage.repository import StaleRevision
        svc, q = af.priced(af.service(LakebaseQuoteRepository(sqlite_db())), asked=8.00)
        q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
        m = {"DBxLab_Sales_Managers"}
        if name == "stale_version_409":
            try:
                svc.approve(q.quote_id, q.revision, "mgr.maria", m, key="a", expected_version=q.version - 1)
                return {"status": 200}
            except StaleRevision:
                return {"status": 409}
        if name == "concurrent_approve_one_wins":
            out, start = [], threading.Barrier(2)

            def go(who):
                start.wait()
                try:
                    svc.approve(q.quote_id, q.revision, who, m, key=who, expected_version=q.version)
                    out.append("APPROVED")
                except StaleRevision:
                    out.append("409")
            ts = [threading.Thread(target=go, args=(w,)) for w in ("mgr.maria", "mgr.omar")]
            [t.start() for t in ts]
            [t.join() for t in ts]
            return {"results": sorted(out), "approvals": len(svc.repo.approvals(q.quote_id))}
        q = svc.approve(q.quote_id, q.revision, "mgr.maria", m, key="a")
        for _ in range(2):
            svc.issue(q.quote_id, q.revision, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
        return {"issue_events": [e.action for e in svc.repo.events(q.quote_id)].count("issue"),
                "state": svc.repo.get(q.quote_id).state.value}
    if name == "later_date_voids_approval":                   # AI-21
        from quote_ai.rules import approval
        svc, q = af.priced(asked=8.00)
        q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
        q3 = svc.approve(q.quote_id, q.revision, "mgr.maria", {"DBxLab_Sales_Managers"}, key="a")
        q4 = svc.revise_terms(q3.quote_id, q3.revision, "rep.alice", "standard", fx.price_book(), delivery_days=21)
        return {"requirements_version": q4.requirements.version, "version_up": q4.version == q3.version + 1,
                "state": q4.state.value, "can_issue": approval.can_issue(q4, q3.approval)}
    raise KeyError(name)


def reliability_failures(outputs: dict, expected: dict) -> int:
    return sum(outputs.get(k) != v for k, v in expected.items())


# ------------------------------------------------------------------ workflow: the agent on its tasks

def _quote(state: str):
    from quote_ai.fixtures import agents as af
    if state == "confirmed":
        return af.confirmed()
    if state == "priced":
        return af.priced()
    svc, q = af.priced(asked=8.00)
    return svc, svc.submit(q.quote_id, q.revision, "rep.alice", key="s")


OVERLAP = {"type": "function", "function": {"name": "find_gloves", "description": "Find gloves for a customer.",
                                            "parameters": {"type": "object", "properties": {}}}}


def runner(design: str = "one_agent", variant: str = "default", client=None, endpoint: str = "standin", setup=None):
    """case -> the agent's events on a fresh quote. design one_agent: QuoteAgent with every tool (design A); specialists:
    the orchestrator (design B). variant overlapping: the find_gloves tool added (AI-19's break)."""
    from quote_ai.fixtures import agents as af
    from quote_ai.agents import runtime
    from quote_ai.agents.agent import QuoteAgent
    from quote_ai.agents.orchestrator import Orchestrator
    from quote_ai.agents.standin import StandInClient

    def run(case: dict) -> dict:
        svc, q = _quote(case.get("state", "confirmed"))
        genie = runtime.demo_genie
        if case.get("setup") == "genie_down":
            def genie(question):
                raise RuntimeError("paused")
        deps = runtime.demo_deps(genie=genie, live_first=True)
        if case.get("setup") == "gudid_down":
            deps.live_gudid.fetch.down = True
        c = client or StandInClient()
        if design == "specialists":
            agent = Orchestrator(deps, svc, c, endpoint)
        else:
            agent = QuoteAgent(deps=deps, client=c, endpoint=endpoint)
            if variant == "always_playbook":         # AI-19's comparison: the playbook in every prompt
                from quote_ai.agents.tools import SPECS
                agent = QuoteAgent(deps=deps, client=c, endpoint=endpoint, prompt=deps.skills.always_in_prompt(),
                                   tools=[n for n in SPECS if n != "load_skill"])
            if variant == "overlapping":
                agent._toolbox = _with_overlap(agent._toolbox)
        history = []
        before = len(svc.repo.events(q.quote_id))
        for h in case.get("history", []):
            prior = list(agent.events(history + [af.user(h)], q, "rep.alice", retry=af.NO_WAIT))
            history += [af.user(h), {"role": "assistant", "content": prior[-1]["answer"]}]
        start = time.perf_counter()
        events = list(agent.events(history + [af.user(case["message"])], q, "rep.alice", retry=af.NO_WAIT))
        seconds = time.perf_counter() - start
        after = svc.repo.get(q.quote_id)
        done = events[-1]
        return {"tools": [e["name"] for e in events if e["type"] == "step"],
                "results": [{"name": e["name"], "source": e["source"]} for e in events if e["type"] == "tool_result"],
                "proposals": [e["proposal"]["target"] for e in events if e["type"] == "proposal"],
                "answer": done["answer"], "violations": done.get("violations", []),
                "state_changed": (after.state, after.version, after.revision) != (q.state, q.version, q.revision),
                "moves": [(e.from_state.value if e.from_state else None, e.to_state.value)
                          for e in svc.repo.events(q.quote_id)[before:]],          # AI-22: illegal_transitions
                "tokens": done["usage"].get("prompt_tokens", 0) + done["usage"].get("completion_tokens", 0),
                "seconds": seconds,
                "route": next((e["route"] for e in events if e["type"] == "plan"), None)}
    return run


def _with_overlap(make):
    def wrapped(quote, rep):
        box = make(quote, rep)
        specs, run = box.specs, box.run
        box.specs = lambda: [OVERLAP] + specs()
        box.run = lambda n, a: run("search_candidates", a) if n == "find_gloves" else run(n, a)
        return box
    return wrapped


def workflow_scores(outputs: dict, case: dict) -> dict:
    import scorers
    return {fn.__name__: fn(outputs, case) for fn in scorers.WORKFLOW_SCORERS}


def run(suite: str, variant: str = "default", design: str = "one_agent", client=None, endpoint: str = "standin",
        kinds: tuple[str, ...] | None = None) -> dict:
    import scorers
    cases = load(suite)
    if kinds:
        cases = [c for c in cases if c["kind"] in kinds]
    per_case = []
    if suite == "pricing":
        for c in cases:
            per_case.append({"case_id": c["case_id"], "pricing_failures": pricing_failures(predict_pricing(c),
                                                                                           c["expected"])})
    elif suite == "reliability":
        for c in cases:
            per_case.append({"case_id": c["case_id"], "reliability_failures": reliability_failures(
                scenario(c["scenario"]), c["expected"])})
    else:
        go = runner(design, variant, client, endpoint)
        for c in cases:
            out = go(c)
            per_case.append({"case_id": c["case_id"], "kind": c["kind"], "tokens": out["tokens"],
                             "seconds": out["seconds"]} | workflow_scores(out, c))
    names = [k for k in per_case[0] if k not in ("case_id", "kind", "tokens", "seconds")] if per_case else []
    gates = {n: sum(p[n] for p in per_case) for n in names if n in scorers.WORKFLOW_GATES}
    metrics = {n: round(sum(p[n] for p in per_case) / len(per_case), 4) for n in names if n not in gates}
    if suite == "workflow" and per_case:
        metrics["tokens_per_request"] = round(statistics.mean(p["tokens"] for p in per_case), 1)
        metrics["p95_seconds"] = round(sorted(p["seconds"] for p in per_case)[int(0.95 * (len(per_case) - 1))], 4)
    failing = [p for p in per_case if any(p.get(g) for g in gates)]
    return {"suite": suite, "variant": variant, "design": design, "cases": len(cases), "metrics": metrics,
            "gates": gates, "passed": not any(gates.values()), "failing_cases": failing[:20], "per_case": per_case}
