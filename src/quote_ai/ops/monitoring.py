"""Operating QuoteAI (AI-26): freshness, the SLOs, cost per quote, and the one view the Ops page and the operating
dashboard both read, so they can never disagree.

  freshness.sql, cost.sql, monitoring_tables.sql   the SQL, one definition each, in quote_ai_monitoring
  load_slo()                 config/quote_ai/slo.yml: the targets; the issue-time limits stay in policy.yml
  freshness_status(rows)     each freshness row with its limit (a watched source: the SLO; an input: the policy's
                             issue limit) and ok / breach
  slo_daily(usage, scores, cost)  one row per day: quotes, p95 latency, the share under the target, errors, issues
                             refused on stale inputs, the live scores, cost per quote. Python, because p95 is not
                             portable SQL; the live scoring job writes it to quote_ai_monitoring.slo_daily
  ops_view(...)              what /api/ops returns and the dashboard shows: freshness, SLOs, cost by step, a status
  run_local(conn, path, names, now)  the same SQL files on SQLite (tests, the local demo, the Operations suite):
                             timestampdiff and current_timestamp() translated, COMMENT clauses dropped, statements
                             that read system.* tables (or are marked "Workspace only") skipped
  LocalMonitoring            the demo week on SQLite through that SQL (QUOTE_AI_DEMO=1, tests)
  SqlMonitoring              the workspace: the same tables through the app's SQL warehouse

Monitoring holds no PII: no rep, no customer contact, no request text.
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def _config(name: str) -> Path:
    """config/quote_ai/<name> in the repo or the bundle; next to the vendored package in the app
    (_vendor/quote_ai_config, scripts/quote_ai/build_app.py)."""
    for p in (ROOT / "config" / "quote_ai" / name, HERE.parents[1] / "quote_ai_config" / name):
        if p.exists():
            return p
    return ROOT / "config" / "quote_ai" / name


SLO_YML = _config("slo.yml")
POLICY_YML = _config("policy.yml")
FRESHNESS_SQL = HERE / "freshness.sql"
COST_SQL = HERE / "cost.sql"
TABLES_SQL = HERE / "monitoring_tables.sql"
# What the Ops page and the operating dashboard read: the same objects, nothing else (tests/quote_ai/test_operations.py)
OPS_OBJECTS = ("freshness", "slo_daily", "cost_per_quote")
STEPS = ("extraction", "retrieval", "comparison", "agent", "serving", "app", "compute")


def load_slo(path: str | Path = SLO_YML) -> dict:
    import yaml
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def issue_limits(path: str | Path = POLICY_YML) -> dict[str, int]:
    from quote_ai.rules.policy import load_policy
    return dict(load_policy(path).freshness.max_age_hours)


# ------------------------------------------------------------------ freshness

def freshness_status(rows: Iterable[dict], slo: dict | None = None, limits: dict | None = None) -> list[dict]:
    """Each row ({kind, name, as_of, age_hours}) with its limit and ok. A watched source has the SLO (24 h); an input
    has the policy's issue limit (inventory 24 h: older and QuoteService refuses ISSUED); anything else is reported
    with no limit (the full release, the on-demand synthetic files)."""
    slo = slo or load_slo()
    limits = limits if limits is not None else issue_limits()
    watched = set(slo.get("watched_sources", []))
    out = []
    for r in rows:
        limit = (slo["slo"]["freshness_hours"] if r["kind"] == "source" and r["name"] in watched
                 else limits.get(r["name"]) if r["kind"] == "input" else None)
        age = r.get("age_hours")
        out.append(dict(r) | {"limit_hours": limit,
                              "ok": True if limit is None else (age is not None and age <= limit)})
    return sorted(out, key=lambda r: (r["kind"] != "source", r["name"]))


def inputs_as_of(rows: Iterable[dict]) -> dict[str, datetime]:
    """The freshness view's inputs as QuoteService.issue takes them ({gudid, evidence, inventory, price_book}: as-of,
    UTC); the evidence snapshot is as old as the GUDID copy it was built from (as the app's sql_inputs_as_of)."""
    from datetime import timezone
    out = {}
    for r in rows:
        if r["kind"] == "input" and r.get("as_of"):
            v = r["as_of"] if isinstance(r["as_of"], datetime) else datetime.fromisoformat(str(r["as_of"]))
            out[r["name"]] = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if "gudid" in out:
        out.setdefault("evidence", out["gudid"])
    return out


def breaches(freshness: Iterable[dict]) -> list[str]:
    """The rows past their limit, as the alert and the Ops page name them: "gudid_daily_updates 30 h (limit 24 h)"."""
    return [f"{r['name']} {r['age_hours']} h (limit {r['limit_hours']} h)" for r in freshness if not r["ok"]]


# ------------------------------------------------------------------ the SLOs per day

def p95(values: list[float]) -> float | None:
    """The nearest-rank 95th percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def _day(v) -> str:
    return v.isoformat()[:10] if isinstance(v, (date, datetime)) else str(v)[:10]


def slo_daily(usage: Iterable[dict], scores: Iterable[dict] = (), cost: Iterable[dict] = (),
              slo: dict | None = None) -> list[dict]:
    """One row per day from the trace usage (latency per quote = the sum of its steps), the live scores and the cost
    per quote. Each target from slo.yml is checked into `ok`."""
    t = (slo or load_slo())["slo"]
    per_quote: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    errors, refused = defaultdict(int), defaultdict(int)
    for u in usage:
        d = _day(u["day"])
        per_quote[d][u["quote_id"]] += (u.get("latency_ms") or 0) / 1000
        errors[d] += u.get("status") == "error"
        refused[d] += u.get("status") == "stale_refused"
    sc: dict[str, dict[str, float]] = defaultdict(dict)
    for s in scores:
        sc[_day(s["day"])][s["scorer"]] = s["value"]
    cost_day: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for c in cost:
        cost_day[_day(c["day"])][c["quote_id"]] += c["cost_usd"]
    out = []
    for d in sorted(per_quote):
        lat = list(per_quote[d].values())
        under = sum(1 for x in lat if x <= t["latency_s"]) / len(lat)
        costs = list(cost_day[d].values())
        row = {"day": d, "quotes": len(lat), "p95_latency_s": round(p95(lat), 2), "under_target_share": round(under, 3),
               "errors": errors[d], "stale_refusals": refused[d],
               "grounded_rate": sc[d].get("grounded_rate"), "eligibility_violations": sc[d].get("eligibility_violations"),
               "unknown_share": sc[d].get("unknown_share"),
               "cost_per_quote_usd": round(sum(costs) / len(costs), 4) if costs else None}
        checks = [under >= t["latency_share"], refused[d] <= t["stale_refusals"],
                  row["eligibility_violations"] in (None, 0) or row["eligibility_violations"] <= t["eligibility_violations"],
                  row["grounded_rate"] is None or row["grounded_rate"] >= t["grounded_rate"],
                  row["unknown_share"] is None or row["unknown_share"] <= t["unknown_share"],
                  row["cost_per_quote_usd"] is None or row["cost_per_quote_usd"] <= t["cost_per_quote_usd"]]
        out.append(row | {"ok": all(checks)})
    return out


def cost_by_step(cost: Iterable[dict]) -> dict:
    """The average cost per quote, and its split by step (extraction ... app, compute)."""
    rows = list(cost)
    quotes = {(r["day"], r["quote_id"]) for r in rows}
    if not quotes:
        return {"quotes": 0, "per_quote_usd": None, "by_step_usd": {}}
    steps: dict[str, float] = defaultdict(float)
    for r in rows:
        steps[r["step"]] += r["cost_usd"]
    n = len(quotes)
    return {"quotes": n, "per_quote_usd": round(sum(steps.values()) / n, 4),
            "by_step_usd": {s: round(v / n, 4) for s, v in sorted(steps.items(), key=lambda kv: -kv[1])}}


def ops_view(freshness: list[dict], slo_rows: list[dict], cost: list[dict], quote_id: str | None = None) -> dict:
    """The Ops page and the dashboard: freshness with its limits, the SLO days, cost per quote by step (and one
    quote's cost, for the capstone's "open the hood"). Status red when a source is past its limit or a day missed."""
    out = {"freshness": freshness, "breaches": breaches(freshness), "slo": slo_rows, "cost": cost_by_step(cost),
           "status": "red" if breaches(freshness) or (slo_rows and not slo_rows[-1]["ok"]) else "green",
           "label": "Demo data (synthetic traffic)"}
    if quote_id:
        out["quote_cost"] = cost_by_step([c for c in cost if c["quote_id"] == quote_id])
    return out


# ------------------------------------------------------------------ the same SQL on SQLite

_TSDIFF = re.compile(r"timestampdiff\(\s*HOUR\s*,\s*(.+?)\s*,\s*current_timestamp\(\)\s*\)", re.I | re.S)
_COMMENT = re.compile(r"\s+COMMENT\s+'(?:[^']|'')*'", re.I)
_VIEW = re.compile(r"CREATE\s+OR\s+REPLACE\s+VIEW\s+([\w.]+)", re.I)


def to_sqlite(stmt: str, now: datetime) -> str:
    """One Databricks SQL statement as SQLite runs it. Only what the monitoring SQL uses is translated."""
    ts = now.strftime("%Y-%m-%d %H:%M:%S")
    s = _COMMENT.sub("", stmt)
    s = _TSDIFF.sub(lambda m: f"CAST((julianday('{ts}') - julianday({m.group(1)})) * 24 AS INTEGER)", s)
    s = re.sub(r"current_timestamp\(\)", f"'{ts}'", s, flags=re.I)
    s = _VIEW.sub(r"CREATE VIEW \1", s)
    return flat(s)


def workspace_only(stmt: str) -> bool:
    """Reads a system table (in its SQL, not its comments), or is marked "-- Workspace only" just above it."""
    body = _COMMENT.sub("", " ".join(l for l in stmt.splitlines() if not l.strip().startswith("--")))
    marked = any(l.strip().startswith("-- Workspace only") for l in stmt.splitlines())
    return "system." in body or marked


LOCAL_SCHEMAS = ("ops", "product", "gudid_gold", "sales", "monitoring")
_SCHEMA_REF = re.compile(r"\b(" + "|".join(LOCAL_SCHEMAS) + r")\.(\w+)")


def flat(sql: str) -> str:
    """monitoring.freshness -> monitoring__freshness: one SQLite database (a view can't span attached ones)."""
    return _SCHEMA_REF.sub(r"\1__\2", sql)


def local_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def local_names() -> dict[str, str]:
    return {s: s for s in LOCAL_SCHEMAS}


def run_local(conn: sqlite3.Connection, path: str | Path, now: datetime, names: dict | None = None) -> int:
    """Run a monitoring SQL file on SQLite: the statements that create views are dropped first; the workspace-only
    ones are skipped. Returns the number of statements run."""
    from quote_ai.storage import sql_files
    n = 0
    for stmt in sql_files.load(path, **(names or local_names())):
        if workspace_only(stmt):
            continue
        m = _VIEW.search(stmt)
        if m:
            conn.execute(f"DROP VIEW IF EXISTS {flat(m.group(1))}")
        conn.execute(to_sqlite(stmt, now))
        n += 1
    return n


def _rows(conn, sql: str) -> list[dict]:
    return [dict(r) for r in conn.execute(sql).fetchall()]


def insert(conn, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0])
    conn.executemany(f"INSERT INTO {flat(table)} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     [tuple(_sqlite_value(r[c]) for c in cols) for r in rows])


def _sqlite_value(v):
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, date):
        return v.isoformat()
    return v


def model_price_rows(config: dict | None = None) -> list[dict]:
    """config/quote_ai/models.yml prices as model_prices rows: one source of truth with the experiments (AI-23)."""
    from quote_ai.llm.client import models_config
    prices = (config or models_config())["prices"]
    return [{"service": s, "input_per_1k": p["input"], "output_per_1k": p["output"]} for s, p in prices.items()]


class LocalMonitoring:
    """The monitoring schema on SQLite, filled from fixtures through the real SQL files (the demo, the tests)."""

    def __init__(self, data: dict[str, list[dict]], now: datetime, slo: dict | None = None) -> None:
        self.now, self.slo = now, slo or load_slo()
        self.conn = local_db()
        for path in (TABLES_SQL, COST_SQL):
            run_local(self.conn, path, now)
        for table, rows in data.items():
            if table.startswith("monitoring."):
                insert(self.conn, table, rows)
            else:
                cols = ", ".join(rows[0]) if rows else ""
                self.conn.execute(f"CREATE TABLE IF NOT EXISTS {flat(table)} ({cols})")
                insert(self.conn, table, rows)
        if not any(t == "monitoring.model_prices" for t in data):
            insert(self.conn, "monitoring.model_prices", model_price_rows())
        run_local(self.conn, FRESHNESS_SQL, now)
        insert(self.conn, "monitoring.slo_daily", slo_daily(
            self.table("monitoring.trace_usage"), self.table("monitoring.live_scores"),
            self.table("monitoring.cost_per_quote"), self.slo))

    def table(self, name: str) -> list[dict]:
        return _rows(self.conn, f"SELECT * FROM {flat(name)}")

    def freshness(self) -> list[dict]:
        return freshness_status(self.table("monitoring.freshness"), self.slo)

    def slo_rows(self) -> list[dict]:
        rows = self.table("monitoring.slo_daily")
        for r in rows:
            r["ok"] = bool(r["ok"])
        return sorted(rows, key=lambda r: r["day"])

    def view(self, quote_id: str | None = None) -> dict:
        return ops_view(self.freshness(), self.slo_rows(), self.table("monitoring.cost_per_quote"), quote_id)


class SqlMonitoring:
    """The workspace: quote_ai_monitoring through a SQL warehouse (the app's, or the rep's token: no PII either way).
    The tables are written by the quote_ai_monitors, quote_ai_gudid_daily and quote_ai_live_scoring jobs."""

    def __init__(self, sql: Any, names: dict[str, str], slo: dict | None = None, days: int = 7) -> None:
        self.sql, self.m, self.slo, self.days = sql, names["monitoring"], slo or load_slo(), days

    def view(self, quote_id: str | None = None) -> dict:
        fresh = freshness_status(self.sql.query(f"SELECT * FROM {self.m}.freshness"), self.slo)
        slo_rows = self.sql.query(f"SELECT * FROM {self.m}.slo_daily WHERE day >= date_sub(current_date(), {self.days}) "
                                  f"ORDER BY day")
        cost = self.sql.query(f"SELECT * FROM {self.m}.cost_per_quote WHERE day >= date_sub(current_date(), "
                              f"{self.days})")
        for r in slo_rows:
            r["day"] = _day(r["day"])
        return ops_view(fresh, slo_rows, cost, quote_id)
