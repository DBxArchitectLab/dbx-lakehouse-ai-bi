"""Job entry point for operations (AI-26): resources/quote_ai/quote_ai_monitors.yml, quote_ai_gudid_daily.job.yml and
quote_ai_live_scoring.job.yml.

  setup      the monitoring tables and views (src/quote_ai/ops/monitoring_tables.sql, cost.sql, freshness.sql) and the
             model prices from config/quote_ai/models.yml
  freshness  the freshness view, each row against its limit (config/quote_ai/slo.yml); prints the breaches. With
             --fail-on-breach the task fails, so the daily job's run turns red with the source named
  cost       the last seven days of compute from the billing system tables (cost.sql)
  score      live scoring of yesterday's production traces (quote_ai.ops.live_scoring): live_scores, trace_usage, then
             slo_daily for that day   --experiment <the app's traces experiment>  [--judge-endpoint]
  alerts     the SQL alerts on freshness and cost per quote, routed to the platform team
  grants     SELECT on what the Ops page and the dashboard read (freshness, slo_daily, cost_per_quote) for the app's
             service principal and the sales managers; nothing else in quote_ai_monitoring

Common: --catalog, --schemas logical=physical,... (from ${resources.schemas.<key>.name}).
Needs Databricks (Spark); the SQL and the logic are tested on fixtures in tests/quote_ai/test_operations.py.
"""
import argparse
import inspect
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.ops import monitoring  # noqa: E402
from quote_ai.storage import sql_files  # noqa: E402


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "freshness", "cost", "score", "alerts", "grants"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--fail-on-breach", action="store_true")
    p.add_argument("--experiment", help="score: the app's traces experiment")
    p.add_argument("--judge-endpoint", help="score: the judge's model service (Unity Gateway)")
    p.add_argument("--warehouse-id", help="alerts: the warehouse the alerts run on")
    p.add_argument("--notify", default="", help="alerts: the destination (the platform team)")
    p.add_argument("--app-sp", help="grants: the app's service principal (application ID)")
    p.add_argument("--managers", default="DBxLab_Sales_Managers", help="grants: the sales managers' group")
    return p.parse_args(argv)


def _spark():
    from pyspark.sql import SparkSession
    return SparkSession.builder.getOrCreate()


def _run(spark, path: Path, names: dict, only_workspace: bool = False) -> int:
    n = 0
    for stmt in sql_files.load(path, **names):
        if only_workspace and not monitoring.workspace_only(stmt):
            continue
        spark.sql(stmt)
        n += 1
    return n


def _append(spark, table: str, rows: list[dict]) -> None:
    if rows:
        spark.createDataFrame(rows, spark.table(table).schema).write.mode("append").insertInto(table)


def setup(spark, names: dict) -> None:
    for path in (monitoring.TABLES_SQL, monitoring.COST_SQL, monitoring.FRESHNESS_SQL):
        print(path.name, _run(spark, path, names), "statements")
    table = f"{names['monitoring']}.model_prices"
    spark.createDataFrame(monitoring.model_price_rows(), spark.table(table).schema).write.mode("overwrite") \
        .insertInto(table, overwrite=True)


def freshness(spark, names: dict, fail: bool) -> int:
    rows = [r.asDict() for r in spark.sql(f"SELECT * FROM {names['monitoring']}.freshness").collect()]
    status = monitoring.freshness_status(rows)
    print(json.dumps(status, default=str, indent=1))
    bad = monitoring.breaches(status)
    if bad:
        print("FRESHNESS BREACH: " + "; ".join(bad) + "  (docs/quote_ai/runbooks/incidents.md)")
    return 1 if bad and fail else 0


def score(spark, names: dict, experiment: str, judge_endpoint: str | None) -> None:
    from quote_ai.ops import live_scoring
    slo = monitoring.load_slo()
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    since = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    if judge_endpoint:
        os.environ["QUOTE_AI_JUDGE_ENDPOINT"] = judge_endpoint
    traces = live_scoring.search_traces(experiment, since)
    scores, usage = live_scoring.run(traces, day, slo["live_scoring"]["sample_size"], slo["live_scoring"]["seed"],
                                     judge=live_scoring.mlflow_judge() if judge_endpoint else None)
    m = names["monitoring"]
    spark.sql(f"DELETE FROM {m}.live_scores WHERE day = '{day}'")
    spark.sql(f"DELETE FROM {m}.trace_usage WHERE day = '{day}'")
    _append(spark, f"{m}.live_scores", [s | {"day": since.date()} for s in scores])
    _append(spark, f"{m}.trace_usage", [u | {"day": since.date()} for u in usage])
    cost = [r.asDict() for r in spark.sql(f"SELECT * FROM {m}.cost_per_quote WHERE day = '{day}'").collect()]
    rows = monitoring.slo_daily(usage, scores, cost, slo)
    spark.sql(f"DELETE FROM {m}.slo_daily WHERE day = '{day}'")
    _append(spark, f"{m}.slo_daily", [r | {"day": since.date()} for r in rows])
    print(json.dumps({"day": day, "traces": len(traces), "scores": scores, "slo": rows}, default=str, indent=1))


ALERTS = {
    "quote_ai_freshness": ("SELECT count(*) AS breaches FROM {monitoring}.freshness "
                           "WHERE kind = 'source' AND name IN ({watched}) AND age_hours > {hours}", "breaches", 0),
    "quote_ai_cost_per_quote": ("SELECT avg(cost) AS cost_per_quote FROM (SELECT quote_id, sum(cost_usd) AS cost "
                                "FROM {monitoring}.cost_per_quote WHERE day = date_sub(current_date(), 1) "
                                "GROUP BY quote_id)", "cost_per_quote", None),
}


def alert_queries(names: dict, slo: dict | None = None) -> dict[str, tuple[str, str, float]]:
    """The alerts' SQL and thresholds from slo.yml: fires when the value is above the threshold."""
    slo = slo or monitoring.load_slo()
    watched = ", ".join(f"'{s}'" for s in slo["watched_sources"])
    out = {}
    for name, (sql, column, threshold) in ALERTS.items():
        q = sql.format(monitoring=names["monitoring"], watched=watched, hours=slo["slo"]["freshness_hours"])
        out[name] = (q, column, slo["slo"]["cost_per_quote_usd"] if threshold is None else threshold)
    return out


def create_alerts(names: dict, warehouse_id: str, notify: str) -> None:
    """Verify before recording: the SQL alerts API in the installed SDK (w.alerts.create with a query_text,
    condition and notification), or the bundle's alert resource if the CLI has one; owner the platform team."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service import sql as s
    w = WorkspaceClient()
    existing = {a.display_name: a for a in w.alerts.list()}
    for name, (query, column, threshold) in alert_queries(names).items():
        if name in existing:
            print(name, "exists")
            continue
        w.alerts.create(alert=s.CreateAlertRequestAlert(
            display_name=name, query_text=query, warehouse_id=warehouse_id,
            condition=s.AlertCondition(op=s.AlertOperator.GREATER_THAN,
                                       operand=s.AlertConditionOperand(column=s.AlertOperandColumn(name=column)),
                                       threshold=s.AlertConditionThreshold(
                                           value=s.AlertOperandValue(double_value=threshold))),
            custom_body=f"QuoteAI {name}: see docs/quote_ai/runbooks/incidents.md (owner: {notify or 'platform team'})"))
        print(name, "created")


def monitoring_grants(names: dict, app_sp: str, managers: str) -> list[str]:
    """Read access to the three objects the Ops page and the operating dashboard read: no PII in them, and nothing
    else in the schema (trace_usage keeps trace IDs)."""
    if not app_sp or not all(c.isalnum() or c == "-" for c in app_sp):
        raise SystemExit(f"not an application ID: {app_sp!r}")
    if not managers or not all(c.isalnum() or c in "_- ." for c in managers):
        raise SystemExit(f"not a group name: {managers!r}")
    m = names["monitoring"]
    out = []
    for who in (f"`{app_sp}`", f"`{managers}`"):
        out += [f"GRANT USE CATALOG ON CATALOG {names['catalog']} TO {who}", f"GRANT USE SCHEMA ON SCHEMA {m} TO {who}"]
        out += [f"GRANT SELECT ON TABLE {m}.{t} TO {who}" for t in monitoring.OPS_OBJECTS]
    return out


def main(argv=None) -> int:
    a = parse(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "alerts":
        create_alerts(names, a.warehouse_id, a.notify)
        return 0
    spark = _spark()
    if a.command == "grants":
        for stmt in monitoring_grants(names, a.app_sp, a.managers):
            spark.sql(stmt)
        return 0
    if a.command == "setup":
        setup(spark, names)
    elif a.command == "freshness":
        return freshness(spark, names, a.fail_on_breach)
    elif a.command == "cost":
        print("cost", _run(spark, monitoring.COST_SQL, names, only_workspace=True), "statements")
    else:
        score(spark, names, a.experiment, a.judge_endpoint)
    return 0


if __name__ == "__main__":
    sys.exit(main())
