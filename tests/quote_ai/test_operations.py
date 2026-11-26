"""Operations (AI-26): the freshness and cost queries on fixtures (the real SQL files on SQLite), the daily SLOs, live
scoring with the gate's own scorers, the daily job as an orchestrator of the existing jobs, and the Operations suite:
a glove leaving distribution, Glove B's report arriving, and no issue on stale inputs."""
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest
import yaml

from quote_ai.fixtures import operations as op
from quote_ai.fixtures.agents import priced
from quote_ai.fixtures.lakeside import NOW
from quote_ai.llm.client import cost_usd
from quote_ai.ops import live_scoring, monitoring
from quote_ai.services.quote_service import StaleEvidence
from quote_ai.storage import sql_files

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "evaluation" / "quote_ai"
RES = ROOT / "resources" / "quote_ai"


def ops_view(**kw):
    return monitoring.LocalMonitoring(op.monitoring_data(**kw), NOW)


# ------------------------------------------------------------------ freshness

def test_a_normal_morning_every_watched_source_is_under_24_hours():
    m = ops_view()
    fresh = {r["name"]: r for r in m.freshness()}
    assert fresh["gudid_daily_updates"]["age_hours"] == 2 and fresh["gudid_daily_updates"]["limit_hours"] == 24
    assert fresh["supplier_documents"]["age_hours"] == 0
    assert fresh["gudid_full_release"]["limit_hours"] is None and fresh["gudid_full_release"]["ok"]   # reported only
    assert fresh["inventory"]["limit_hours"] == 24 and fresh["price_book"]["limit_hours"] == 168      # policy.yml
    assert m.view()["status"] == "green" and m.view()["breaches"] == []


def test_a_failed_run_does_not_count_as_fresh():
    rows = monitoring.LocalMonitoring(op.monitoring_data(), NOW).table("monitoring.freshness")
    daily = next(r for r in rows if r["name"] == "gudid_daily_updates")
    assert daily["as_of"] == "2026-11-14 06:20:00"                     # the 08:10 failed run is ignored


def test_the_job_paused_30_hours_the_alert_names_each_stale_source():
    v = ops_view(paused_hours=30).view()
    assert v["status"] == "red"
    assert v["breaches"] == ["gudid_api_device_history 30 h (limit 24 h)", "gudid_daily_updates 30 h (limit 24 h)",
                             "inventory 30 h (limit 24 h)"]


def test_a_signed_off_document_waiting_for_a_day_breaches():
    assert ops_view(document_waiting_hours=30).view()["breaches"] == ["supplier_documents 30 h (limit 24 h)"]
    assert ops_view(document_waiting_hours=5).view()["breaches"] == []


def test_issue_is_refused_on_the_inputs_the_freshness_view_reports():
    as_of = monitoring.inputs_as_of(ops_view(paused_hours=30).table("monitoring.freshness"))
    assert set(as_of) == {"gudid", "evidence", "inventory", "price_book"}
    svc, q = priced(asked=8.40)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    with pytest.raises(StaleEvidence, match="inventory as of 2026-11-13 03:00 UTC"):
        svc.issue(q.quote_id, q.revision, "rep.alice", key="i", inputs_as_of=as_of)
    fresh = monitoring.inputs_as_of(ops_view().table("monitoring.freshness"))
    assert svc.issue(q.quote_id, q.revision, "rep.alice", key="i2", inputs_as_of=fresh).state.value == "ISSUED"


# ------------------------------------------------------------------ cost per quote

def test_model_cost_per_step_is_the_models_yml_price():
    m = ops_view()
    usage = [u for u in m.table("monitoring.trace_usage") if u["step"] == "comparison"][0]
    row = next(c for c in m.table("monitoring.cost_per_quote")
               if c["quote_id"] == usage["quote_id"] and c["step"] == "comparison")
    assert row["cost_usd"] == pytest.approx(cost_usd("quote_ai_chat", usage["prompt_tokens"],
                                                     usage["completion_tokens"]))


def test_the_apps_compute_is_spread_over_each_days_quotes():
    data = op.monitoring_data()
    rows = monitoring.LocalMonitoring(data, NOW).table("monitoring.cost_per_quote")
    day = data["monitoring.daily_compute"][0]["day"]
    quotes = {r["quote_id"] for r in rows if r["day"] == day}
    app = [r["cost_usd"] for r in rows if r["day"] == day and r["step"] == "app"]
    assert len(app) == len(quotes) and sum(app) == pytest.approx(1.20)
    summary = monitoring.cost_by_step(rows)
    assert set(summary["by_step_usd"]) == {"extraction", "retrieval", "comparison", "agent", "app", "serving",
                                           "compute"}
    assert summary["per_quote_usd"] == pytest.approx(sum(summary["by_step_usd"].values()), abs=1e-3)


def test_one_quotes_cost_for_open_the_hood():
    v = ops_view().view(quote_id="Q-demo-1113-0")
    assert v["quote_cost"]["quotes"] == 1 and "app" in v["quote_cost"]["by_step_usd"]


def test_workspace_only_statements_are_skipped_locally_and_rendered_for_the_bundle():
    stmts = sql_files.load(monitoring.COST_SQL, **sql_files.names_for("dbxarchitectlab_prod", {}))
    ws = [s for s in stmts if monitoring.workspace_only(s)]
    assert len(ws) == 2 and "system.billing.usage" in ws[1] and "DELETE FROM" in ws[0]
    assert "dbxarchitectlab_prod.quote_ai_monitoring.daily_compute" in ws[1]
    assert not any(monitoring.workspace_only(s) for s in sql_files.load(monitoring.FRESHNESS_SQL,
                                                                         **monitoring.local_names()))


def test_the_freshness_sql_reads_the_ai02_log_columns():
    from quote_ai.ingest.log import DDL
    sql = monitoring.FRESHNESS_SQL.read_text(encoding="utf-8")
    assert "ended_at" in DDL["ingestion_log"] and "max(ended_at)" in sql and "{ops}.ingestion_log" in sql


# ------------------------------------------------------------------ SLOs per day

def test_p95_is_the_nearest_rank():
    assert monitoring.p95(list(range(1, 21))) == 19 and monitoring.p95([]) is None


def test_a_day_misses_its_slo_on_latency_or_a_stale_refusal():
    usage = [{"day": "2026-11-13", "quote_id": f"Q{i}", "latency_ms": 5000, "status": "ok"} for i in range(19)]
    ok, = monitoring.slo_daily(usage)
    assert ok["ok"] and ok["p95_latency_s"] == 5.0
    slow, = monitoring.slo_daily(usage + [{"day": "2026-11-13", "quote_id": "Q19", "latency_ms": 30000}] * 1
                                 + [{"day": "2026-11-13", "quote_id": "Q20", "latency_ms": 30000}])
    assert slow["under_target_share"] < 0.95 and not slow["ok"]
    refused, = monitoring.slo_daily(usage + [{"day": "2026-11-13", "quote_id": "Q1", "latency_ms": 1,
                                              "status": "stale_refused"}])
    assert refused["stale_refusals"] == 1 and not refused["ok"]
    low, = monitoring.slo_daily(usage, [{"day": "2026-11-13", "scorer": "grounded_rate", "value": 0.8}])
    assert not low["ok"]


def test_the_demo_week_shows_one_missed_day_and_a_green_today():
    v = ops_view().view()
    assert len(v["slo"]) == 7 and [d["ok"] for d in v["slo"]].count(False) == 1 and v["slo"][-1]["ok"]
    assert v["label"].startswith("Demo data")


# ------------------------------------------------------------------ live scoring

def test_live_scoring_uses_the_gates_scorers_not_a_copy():
    sys.path.insert(0, str(EVAL))
    import scorers
    assert live_scoring.gate_scorers() is scorers


def test_the_sample_is_deterministic_and_capped():
    traces = [{"trace_id": f"t{i:03d}"} for i in range(200)]
    a, b = live_scoring.sample(traces, 50, 1), live_scoring.sample(list(reversed(traces)), 50, 1)
    assert len(a) == 50 and a == b
    assert live_scoring.sample(traces[:10], 50, 1) == traces[:10]


def test_run_writes_scores_for_the_day_and_usage_without_text():
    case = next(c for c in json.loads("[" + ",".join((EVAL / "v1" / "operations" / "cases.jsonl").read_text(
        encoding="utf-8").split("\n")[:-1]) + "]") if c["kind"] == "live_scoring")
    traces = [t | {"spans": [{"step": "comparison", "service": "quote_ai_chat", "prompt_tokens": 10,
                              "completion_tokens": 5, "latency_ms": 900}]} for t in case["traces"]]
    scores, usage = live_scoring.run(traces, "2026-11-13")
    assert {s["scorer"] for s in scores} == {"eligibility_violations", "unknown_as_match", "unknown_share",
                                             "layer_cited"}                 # no judge given: no grounded_rate
    assert len(usage) == 3 and set(usage[0]) == {"day", "quote_id", "trace_id", "step", "service", "prompt_tokens",
                                                 "completion_tokens", "latency_ms", "status"}
    judged, _ = live_scoring.run(traces, "2026-11-13", judge=lambda outs: 0.9)
    assert {s["scorer"]: s["value"] for s in judged}["grounded_rate"] == 0.9


# ------------------------------------------------------------------ the jobs, the dashboard, the grants

def _job(file, key):
    return yaml.safe_load((RES / file).read_text(encoding="utf-8"))["resources"]["jobs"][key]


def test_the_daily_job_chains_the_existing_jobs_in_order():
    job = _job("quote_ai_gudid_daily.job.yml", "quote_ai_gudid_daily")
    tasks = {t["task_key"]: t for t in job["tasks"]}
    assert list(tasks) == ["ingest", "gudid", "evidence", "documents", "search_doc", "index_sync", "freshness"]
    assert tasks["ingest"]["run_job_task"]["job_id"] == "${resources.jobs.quote_ai_ingest_daily.id}"
    assert tasks["gudid"]["pipeline_task"]["pipeline_id"] == "${resources.pipelines.quote_ai_gudid.id}"
    assert tasks["evidence"]["pipeline_task"]["pipeline_id"] == "${resources.pipelines.quote_ai_attributes.id}"
    assert tasks["index_sync"]["spark_python_task"]["parameters"][:2] == ["index", "--what"]
    assert tasks["freshness"]["run_if"] == "ALL_DONE" and "--fail-on-breach" in tasks["freshness"][
        "spark_python_task"]["parameters"]
    assert job["schedule"]["timezone_id"] == "UTC" and job["tags"]["app"] == "quote_ai"


def test_the_ingestion_job_is_untouched():
    text = (RES / "quote_ai_ingest_daily.job.yml").read_text(encoding="utf-8")
    assert text.startswith("# GENERATED from config/quote_ai/ingest/sources.yml")
    assert "quote_ai_gudid_daily" not in text


def test_the_dashboard_reads_only_what_the_ops_page_reads():
    from quote_ai.semantic import dashboard
    d = dashboard.load(ROOT / "src" / "quote_ai" / "semantic" / "operations.lvdash.json")
    tables = {t for ds in d["datasets"] for t in dashboard._FROM.findall(dashboard.query(ds))}
    assert tables == set(monitoring.OPS_OBJECTS)
    names = {ds["name"] for ds in d["datasets"]}
    for item in d["pages"][0]["layout"]:
        for q in item["widget"].get("queries", []):
            assert q["query"]["datasetName"] in names


def test_alerts_and_grants_cover_only_the_ops_objects():
    sys.path.insert(0, str(ROOT / "src" / "quote_ai" / "jobs"))
    import ops_job
    names = sql_files.names_for("dbxarchitectlab_prod", {})
    alerts = ops_job.alert_queries(names)
    q, column, threshold = alerts["quote_ai_freshness"]
    assert "'gudid_daily_updates'" in q and "age_hours > 24" in q and threshold == 0
    assert alerts["quote_ai_cost_per_quote"][2] == monitoring.load_slo()["slo"]["cost_per_quote_usd"]
    grants = ops_job.monitoring_grants(names, "abc-123", "DBxLab_Sales_Managers")
    selects = [g for g in grants if g.startswith("GRANT SELECT")]
    assert len(selects) == 6 and all(any(f".{o} " in g for o in monitoring.OPS_OBJECTS) for g in selects)
    with pytest.raises(SystemExit):
        ops_job.monitoring_grants(names, "x; DROP", "g")


# ------------------------------------------------------------------ the Operations suite

def test_the_operations_suite_is_required_and_passes():
    sys.path.insert(0, str(EVAL))
    import gate
    import operations
    spec = gate.load_thresholds()["operations"]
    assert spec["required"] and spec["runs"] == "ci"
    r = operations.run()
    res = gate.check("operations", spec, {k: r[k] for k in operations.METRICS})
    assert res["passed"], r["per_case"]


def test_a_glove_leaving_distribution_is_no_longer_offered():
    sys.path.insert(0, str(EVAL))
    import operations
    case = next(c for c in operations.load() if c["kind"] == "distribution_change")
    r = operations.distribution_change(case)
    assert r["offered_before"] and not r["offered_after"]
    assert r["excluded_with"] == "verified_mismatch" and not r["candidate_after_sync"]


def test_glove_b_is_eligible_on_the_first_run_after_its_report_and_both_cases_still_pass():
    sys.path.insert(0, str(EVAL))
    import operations
    import season4
    case = next(c for c in operations.load() if c["kind"] == "report_arrives")
    assert operations.report_arrives(case) | {"case_id": None} == {"case_id": None, "before": "needs_evidence",
                                                                   "after": "eligible", "report_not_reflected": 0}
    from quote_ai.fixtures import operations as fx_op
    assert fx_op.report_arrives().snapshot() in season4.SNAPSHOTS
    cases = {c["case_id"]: c for c in season4.load("eligibility")}
    assert cases["E-014"]["evidence_snapshot"].endswith("v41") and cases["E-015"]["evidence_snapshot"].endswith("v57")


def test_the_freshness_window_is_relative_to_now():
    later = NOW + timedelta(hours=30)
    v = monitoring.LocalMonitoring(op.monitoring_data(NOW), later).view()
    assert "gudid_daily_updates 32 h (limit 24 h)" in v["breaches"]
