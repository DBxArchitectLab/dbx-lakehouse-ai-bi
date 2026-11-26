"""The Operations suite (AI-26): declared in AI-22, required from AI-26. Each case runs the code production runs, on
the operations fixtures (quote_ai.fixtures.operations), and counts what must never happen:

  distribution_change   today's GUDID update takes a Lakeside candidate out of distribution; after the daily run
                        (evidence updated, index synced) it is not offered            -> stale_offered
  report_arrives        Glove B's chemo report verified (snapshot v57): eligible on the first run after it, and the
                        case without the report still passes on its own snapshot (eligibility E-014 / E-015)
                                                                                       -> report_not_reflected
  stale_issue           the daily job paused 30 h: the inputs QuoteService checks come from the freshness view; Issue
                        is refused and names the stale input                           -> stale_issued
  freshness             the freshness SQL on the fixture log: exactly the expected breaches -> freshness_wrong
  cost                  cost per quote by step on the fixture week: every quote carries its share of the app's
                        compute, and the day's spread adds up to the day's cost       -> cost_wrong
  live_scoring          the gate's scorers on sampled traces: an eligible glove with an unknown check is counted
                                                                                       -> live_scoring_wrong
In the workspace (--catalog and --warehouse-id), the production numbers too: freshness_breaches now, and whether
cost per quote is reported.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUITE = HERE / "v1" / "operations"


def load() -> list[dict]:
    return [json.loads(l) for l in (SUITE / "cases.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]


def _shortlist(rows: list[dict], docs: list[dict], snapshot: str = "product_evidence@v41"):
    from quote_ai.agents import runtime
    from quote_ai.fixtures.agents import service
    from quote_ai.search import local
    from quote_ai.services.shortlist import LocalRules, build_shortlist
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    from quote_ai.storage.product_catalog import DemoProductCatalog
    store = DemoEvidenceStore(snapshot=snapshot)
    store.rows = rows
    cat = DemoProductCatalog()
    q = runtime.demo_quote(service())
    return build_shortlist(q.requirements, q.lines, local.InMemoryIndex(docs), cat, LocalRules(store, cat))


def _eligible(s) -> set[str]:
    return {r.product_id for r in s.results if r.status.value == "eligible"}


def distribution_change(case: dict) -> dict:
    from quote_ai.fixtures import operations as op
    from quote_ai.search import docs
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    pid = f"GLOVE-{case['glove']}"
    before = DemoEvidenceStore().rows
    after = op.leaves_distribution(before, pid)
    offered_before = pid in _eligible(_shortlist(before, docs.demo_docs(before)))
    evidence_only = _shortlist(after, docs.demo_docs(after))                  # evidence updated, index not yet synced
    synced = _shortlist(after, op.synced_docs(after, pid))                    # the daily run done
    excluded = {r.product_id: r for r in evidence_only.results if r.status.value == "excluded"}
    check = next((c for c in excluded[pid].checks if c.requirement == case["expect"]["check"]), None) \
        if pid in excluded else None
    offered_after = pid in _eligible(synced)
    return {"case_id": case["case_id"], "offered_before": offered_before, "offered_after": offered_after,
            "excluded_with": check.result.value if check else None, "candidate_after_sync": pid in synced.candidates,
            "stale_offered": int(offered_after or (check is None or check.result.value != "verified_mismatch"))}


def report_arrives(case: dict) -> dict:
    from quote_ai.fixtures import operations as op
    from quote_ai.search import docs
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    pid = f"GLOVE-{case['glove']}"
    store = op.report_arrives()
    after = _shortlist(store.rows, docs.demo_docs(store.rows), store.snapshot())
    before = _shortlist(DemoEvidenceStore().rows, docs.demo_docs(DemoEvidenceStore().rows))
    status_after = next((r.status.value for r in after.results if r.product_id == pid), None)
    status_before = next((r.status.value for r in before.results if r.product_id == pid), None)
    ok = status_after == case["expect"]["after"] and status_before == case["expect"]["before"]
    return {"case_id": case["case_id"], "before": status_before, "after": status_after,
            "report_not_reflected": int(not ok)}


def stale_issue(case: dict) -> dict:
    from quote_ai.fixtures import operations as op
    from quote_ai.fixtures.agents import priced
    from quote_ai.fixtures.lakeside import NOW
    from quote_ai.ops import monitoring
    from quote_ai.services.quote_service import StaleEvidence
    view = monitoring.LocalMonitoring(op.monitoring_data(paused_hours=case["paused_hours"]), NOW)
    as_of = monitoring.inputs_as_of(view.table("monitoring.freshness"))
    svc, q = priced(asked=8.40)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    try:
        svc.issue(q.quote_id, q.revision, "rep.alice", key="i", inputs_as_of=as_of)
        return {"case_id": case["case_id"], "stale_issued": 1, "refusal": None}
    except StaleEvidence as e:
        named = all(any(n in s for s in e.stale) for n in case["expect"]["names"])
        return {"case_id": case["case_id"], "stale_issued": int(not named), "refusal": e.stale}


def freshness(case: dict) -> dict:
    from quote_ai.fixtures import operations as op
    from quote_ai.fixtures.lakeside import NOW
    from quote_ai.ops import monitoring
    view = monitoring.LocalMonitoring(op.monitoring_data(paused_hours=case.get("paused_hours"),
                                                         document_waiting_hours=case.get("document_waiting_hours")),
                                      NOW)
    got = sorted(r["name"] for r in view.freshness() if not r["ok"])
    return {"case_id": case["case_id"], "breaches": got,
            "freshness_wrong": int(got != sorted(case["expect"]["breaches"]))}


def cost(case: dict) -> dict:
    from collections import defaultdict

    from quote_ai.fixtures import operations as op
    from quote_ai.fixtures.lakeside import NOW
    from quote_ai.ops import monitoring
    data = op.monitoring_data()
    rows = monitoring.LocalMonitoring(data, NOW).table("monitoring.cost_per_quote")
    quotes = {(r["day"], r["quote_id"]) for r in rows}
    steps = defaultdict(set)
    for r in rows:
        steps[(r["day"], r["quote_id"])].add(r["step"])
    missing = sum(1 for k in quotes if not set(case["expect"]["steps"]) <= steps[k])
    spread_off = 0
    for c in data["monitoring.daily_compute"]:
        got = sum(r["cost_usd"] for r in rows if r["day"] == c["day"] and r["step"] == c["step"])
        spread_off += abs(got - c["cost_usd"]) > 1e-6
    return {"case_id": case["case_id"], "quotes": len(quotes), "missing_steps": missing, "spread_off": spread_off,
            "cost_wrong": int(missing > 0 or spread_off > 0)}


def live_scoring(case: dict) -> dict:
    from quote_ai.ops import live_scoring as ls
    rows = {r["scorer"]: r["value"] for r in ls.score(case["traces"])}
    wrong = sum(1 for k, v in case["expect"].items() if rows.get(k) != v)
    return {"case_id": case["case_id"], "scores": rows, "live_scoring_wrong": wrong}


KINDS = {"distribution_change": distribution_change, "report_arrives": report_arrives, "stale_issue": stale_issue,
         "freshness": freshness, "cost": cost, "live_scoring": live_scoring}
METRICS = ("stale_offered", "report_not_reflected", "stale_issued", "freshness_wrong", "cost_wrong",
           "live_scoring_wrong")


def run(catalog: str | None = None, warehouse_id: str | None = None, schemas: str | None = None) -> dict:
    per_case = [KINDS[c["kind"]](c) for c in load()]
    out = {m: sum(p.get(m, 0) for p in per_case) for m in METRICS} | {"per_case": per_case}
    if catalog and warehouse_id:
        from quote_ai.ops import monitoring
        from quote_ai.storage import sql_files
        from quote_ai.storage.delta_repository import DatabricksSql
        names = sql_files.names_for(catalog, dict(p.split("=", 1) for p in (schemas or "").split(",") if p))
        view = monitoring.SqlMonitoring(DatabricksSql(warehouse_id), names).view()
        out |= {"freshness_breaches": len(view["breaches"]),
                "cost_per_quote_reported": int(view["cost"]["per_quote_usd"] is not None)}
    return out
