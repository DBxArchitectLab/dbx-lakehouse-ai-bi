"""The capstone scorecard (AI-27): every success criterion of the plan (section 10) with its target, its result, a
pass, and the suite or test that proves it. Only glue: the suites are the evaluation package's (run_eval.collect,
gate.check against the same v1/thresholds.yml), the app check is the whole Lakeside flow through the app's API
(capstone/app_flow.py), operations is the Operations suite plus production's own freshness and cost.

  python evaluation/quote_ai/capstone/run_capstone.py                       # locally: the pure-code suites and the
                                                                            # app flow; workspace criteria not_run
  python evaluation/quote_ai/capstone/run_capstone.py --catalog dbxarchitectlab_prod --warehouse-id ID \\
      --results-table dbxarchitectlab_prod.quote_ai_eval.eval_results --table dbxarchitectlab_prod.quote_ai_eval.capstone \\
      --app quote-ai-prod --baseline-minutes 95
In the workspace it is read-only (the quote_ai_capstone job): the pure-code suites run in the job, the workspace suites
(requirements, product facts, Genie, the win model's held-out months, the judge) are read from the latest gate run in
eval_results, freshness and cost from quote_ai_monitoring, and the deployed app's /api/health; the Lakeside flow runs
in-process on the demo data, never as a quote in prod. Exit 1 when a criterion fails; not_run is reported, never a pass.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parents[1] / "src"))

# area, target (plan section 10), proofs: (suite, metric) from the package, or ("app", ...) / ("outcome", ...)
CRITERIA = [
    ("Eligibility", "zero violations; zero unknown as a match; A eligible, B unknown until its report",
     [("eligibility", "eligibility_violations"), ("eligibility", "unknown_as_match"),
      ("eligibility", "eligibility_case_failures"), ("operations", "report_not_reflected")]),
    ("Fulfilment", "100% correct full / partial / backorder", [("fulfilment", "wrong_decision")]),
    ("Mapping", "every offered SKU has a verified mapping",
     [("mapping", "unverified_offered"), ("mapping", "mapping_failures")]),
    ("Product facts", "100% match the source record or document they cite",
     [("product_facts", "match_rate"), ("product_facts", "status_violations")]),
    ("Pricing", "100% correct units, tiers, margins; no discount beyond policy without approval of that version",
     [("pricing", "errors"), ("reliability", "concurrent_approvals_handled")]),
    ("Workflow", "only allowed changes, all through QuoteService; approval history; concurrency and retries handled",
     [("workflow", "illegal_transitions"), ("workflow", "commits_outside_quote_service"),
      ("reliability", "duplicate_effects"), ("reliability", "stale_issue_refused")]),
    ("Requirement extraction", ">= 95% field accuracy", [("requirements", "field_accuracy")]),
    ("Attribute extraction", ">= 90% on hand-labelled records; AI values unverified until confirmed",
     [("product_facts", "min_attribute_accuracy"), ("product_facts", "status_violations")]),
    ("Search", "Recall@5 >= 85%", [("search", "recall_at_5"), ("search", "eligibility_violations")]),
    ("Comparison", ">= 90% supported by cited evidence; unresolved evidence shown",
     [("comparison", "grounded_rate"), ("comparison", "layer_cited"), ("comparison", "no_evidence_failures")]),
    ("Genie analytics", ">= 90% on the fixed benchmark", [("genie", "benchmark")]),
    ("Win prediction", "beats the rule baseline, recovers the planted signal (a simulation on synthetic data)",
     [("win_model", "decision_failures"), ("win_model", "beats_baseline"), ("win_model", "signal_recovered")]),
    ("Security", "zero unauthorized; model access and data authorization tested separately",
     [("data_authorization", "unauthorized"), ("model_access", "unauthorized")]),
    ("Operations", "every request traceable; GUDID updates within one day; cost per quote reported",
     [("operations", "stale_offered"), ("operations", "stale_issued"), ("operations", "freshness_wrong"),
      ("operations", "cost_wrong"), ("production", "freshness_breaches"), ("production", "cost_per_quote_reported")]),
    ("Business outcome", "time to an approved, evidence-backed quote: hours to minutes (measured in the demo)",
     [("outcome", "time_to_quote")]),
    ("App", "every step of the Lakeside quote done in the app; no notebook", [("app", "lakeside_flow")]),
]


def _suite_checks(a, results_table_rows: dict) -> dict[tuple[str, str], dict]:
    """(suite, metric) -> {value, status}. Pure-code suites run here; the others come from the latest gate run
    (eval_results) when given, else not_run."""
    import gate
    import run_eval
    suites = gate.load_thresholds()
    wanted = {s for _, _, proofs in CRITERIA for s, _ in proofs if s in suites}
    ns = run_eval.parser().parse_args([])
    ns.catalog, ns.warehouse_id, ns.schemas = None, None, None      # the workspace suites are read, not re-run
    out = {}
    for name in sorted(wanted):
        spec = suites[name]
        if spec["runs"] == "ci":
            try:
                metrics, _ = run_eval.collect(name, ns)
            except Exception as e:  # noqa: BLE001  a crashed suite fails its metrics
                metrics = {}
                print(f"{name}: {type(e).__name__}: {e}")
            for c in gate.check(name, spec, metrics)["checks"]:
                out[(name, c["metric"])] = {"value": c["value"], "status": c["status"]}
        for metric in spec["thresholds"]:
            if (name, metric) in out and out[(name, metric)]["status"] != "not_run":
                continue
            got = results_table_rows.get((name, metric))
            out[(name, metric)] = got or {"value": None, "status": "not_run"}
    return out


def latest_results(table: str, warehouse_id: str) -> dict[tuple[str, str], dict]:
    """The newest gate run's rows from eval_results (read-only): {(suite, metric): {value, status}}."""
    from quote_ai.storage.delta_repository import DatabricksSql
    if not all(c.isalnum() or c in "._`" for c in table):
        raise ValueError(f"not a table name: {table!r}")
    rows = DatabricksSql(warehouse_id).query(
        f"SELECT suite, metric, value, status FROM {table} WHERE run_id = "
        f"(SELECT run_id FROM {table} ORDER BY run_at DESC LIMIT 1)")
    return {(r["suite"], r["metric"]): {"value": r["value"], "status": r["status"]} for r in rows}


def production(a) -> dict[tuple[str, str], dict]:
    """Freshness and cost from quote_ai_monitoring (read-only), and the deployed app's health."""
    if not (a.catalog and a.warehouse_id):
        return {("production", m): {"value": None, "status": "not_run"}
                for m in ("freshness_breaches", "cost_per_quote_reported")}
    import operations
    r = operations.run(a.catalog, a.warehouse_id, a.schemas)
    out = {("production", "freshness_breaches"): {"value": r["freshness_breaches"],
                                                  "status": "pass" if r["freshness_breaches"] == 0 else "fail"},
           ("production", "cost_per_quote_reported"): {"value": r["cost_per_quote_reported"],
                                                       "status": "pass" if r["cost_per_quote_reported"] else "fail"}}
    return out


def app_and_outcome(a) -> tuple[dict, dict]:
    import app_flow
    flow = app_flow.run()
    checks = {("app", "lakeside_flow"): {"value": f"{len(flow['steps'])} API steps, {len(flow['failed'])} failed",
                                         "status": "pass" if flow["passed"] else "fail"}}
    if a.app:
        from quote_ai.ops import smoke_test
        try:
            ok = smoke_test.workspace_session(a.app).get("/api/health").json().get("status") == "ok"
        except Exception as e:  # noqa: BLE001  the deployed app did not answer
            print(f"{a.app}: {e}")
            ok = False
        checks[("app", "deployed_health")] = {"value": a.app, "status": "pass" if ok else "fail"}
    if a.baseline_minutes:
        measured = bool(a.app_minutes)            # 0 (the job default) means not measured
        minutes = a.app_minutes if measured else flow["seconds"] / 60
        status = "pass" if minutes < a.baseline_minutes else "fail"
        value = (f"{minutes:.1f} min in the app vs {a.baseline_minutes:.0f} min by hand"
                 + ("" if measured else " (the API flow alone; pass --app-minutes from the demo)"))
    else:
        status, value = "not_run", f"{flow['seconds']:.1f} s through the API; by-hand baseline not given"
    checks[("outcome", "time_to_quote")] = {"value": value, "status": status}
    return checks, flow


def scorecard(checks: dict) -> list[dict]:
    rows = []
    for area, target, proofs in CRITERIA:
        found = [(f"{s}.{m}", checks.get((s, m), {"value": None, "status": "not_run"})) for s, m in proofs]
        if area == "App" and ("app", "deployed_health") in checks:
            found.append(("app.deployed_health", checks[("app", "deployed_health")]))
        statuses = {c["status"] for _, c in found}
        status = "fail" if "fail" in statuses else ("not_run" if "not_run" in statuses else "pass")
        result = "; ".join(f"{n}={_fmt(c['value'])}" for n, c in found if c["status"] != "not_run") or "-"
        rows.append({"area": area, "target": target, "result": result, "status": status,
                     "proven_by": ", ".join(n for n, _ in found),
                     "not_run": ", ".join(n for n, c in found if c["status"] == "not_run")})
    return rows


def _fmt(v) -> str:
    return "-" if v is None else (f"{v:.4g}" if isinstance(v, float) else str(v))


def table(rows: list[dict]) -> str:
    lines = [f"{'Area':24} {'Status':8} Result"]
    for r in rows:
        lines.append(f"{r['area']:24} {r['status']:8} {r['result']}" + (f"   (not run: {r['not_run']})"
                                                                         if r["not_run"] else ""))
    return "\n".join(lines)


def write_scorecard(rows: list[dict], target_table: str) -> None:
    """Append the scorecard to quote_ai_eval.capstone (the job). Verify before recording: the job's Spark session."""
    import gate
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    run_id, at = uuid.uuid4().hex[:12], datetime.now(timezone.utc)
    full = [r | {"run_id": run_id, "git_sha": gate.git_sha(), "package_version": gate.package_version(), "run_at": at}
            for r in rows]
    spark.createDataFrame(full).write.mode("append").option("mergeSchema", "true").saveAsTable(target_table)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog")
    p.add_argument("--schemas", default="")
    p.add_argument("--warehouse-id")
    p.add_argument("--results-table", help="the gate's eval_results: the workspace suites' latest run (read-only)")
    p.add_argument("--table", help="write the scorecard here (catalog.quote_ai_eval.capstone)")
    p.add_argument("--app", help="the deployed app (quote-ai-prod): its /api/health")
    p.add_argument("--baseline-minutes", type=float, help="the recorded by-hand time for the Lakeside quote")
    p.add_argument("--app-minutes", type=float, help="the measured in-app time from the demo (default: the API flow)")
    p.add_argument("--json", action="store_true")
    return p


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    prior = latest_results(a.results_table, a.warehouse_id) if a.results_table and a.warehouse_id else {}
    checks = _suite_checks(a, prior)
    checks |= production(a)
    app_checks, flow = app_and_outcome(a)
    checks |= app_checks
    rows = scorecard(checks)
    if a.json:
        print(json.dumps({"scorecard": rows, "flow": {k: flow[k] for k in ("quote_id", "state", "seconds",
                                                                            "failed")}}, default=str, indent=1))
    else:
        print("QuoteAI capstone scorecard (demo data; the win model is an educational simulation)")
        print(table(rows))
    if a.table:
        write_scorecard(rows, a.table)
    failed = [r["area"] for r in rows if r["status"] == "fail"]
    print("CAPSTONE", "FAILED: " + ", ".join(failed) if failed else "PASSED (not_run listed above, never a pass)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
