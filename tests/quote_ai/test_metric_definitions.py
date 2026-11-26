"""The metric definitions (AI-10), known answers on a tiny fixture. The measure expressions are read from the metric
view files themselves and run in SQLite (COUNT_IF, MEDIAN, date_trunc and unix_timestamp added as functions), so a
changed definition changes these answers. The dashboard lint catches a definition copied into a dataset."""
import json
import re
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from quote_ai.mock import sales
from quote_ai.semantic import dashboard
from quote_ai.storage import sql_files

ROOT = Path(__file__).resolve().parents[2]
SEM = ROOT / "src" / "quote_ai" / "semantic"
NAMES = sql_files.names_for("cat", {})


def spec(file: str) -> dict:
    (stmt,) = sql_files.load(SEM / file, **NAMES)
    assert re.search(r"WITH METRICS\s+LANGUAGE YAML", stmt)
    return yaml.safe_load(stmt.split("$$")[1])


QUOTE = spec("quote_metrics.sql")
MEASURES = {m["name"]: m["expr"] for m in QUOTE["measures"]}
DIMS = {d["name"]: d["expr"] for d in QUOTE["dimensions"]}


class CountIf:
    def __init__(self):
        self.n = 0.0                      # a float, like Databricks: the ratio is not integer division

    def step(self, cond):
        self.n += 1 if cond else 0

    def finalize(self):
        return self.n


class Median:
    def __init__(self):
        self.v = []

    def step(self, x):
        if x is not None:
            self.v.append(x)

    def finalize(self):
        return statistics.median(self.v) if self.v else None


def db(rows: list[dict]) -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.create_aggregate("COUNT_IF", 1, CountIf)
    con.create_aggregate("MEDIAN", 1, Median)
    con.create_function("unix_timestamp", 1, lambda t: None if t is None else datetime.fromisoformat(t).timestamp())
    con.create_function("date_trunc", 2, lambda unit, t: None if t is None else t[:7] + "-01")
    cols = list(rows[0])
    con.execute(f"CREATE TABLE quote_fact ({', '.join(cols)})")
    con.executemany(f"INSERT INTO quote_fact VALUES ({', '.join('?' * len(cols))})",
                    [tuple(r[c].isoformat() if isinstance(r[c], datetime) else r[c] for c in cols) for r in rows])
    return con


def measure(con, name: str, by: str | None = None) -> dict:
    """SELECT <dimension>, MEASURE(<name>) FROM quote_metrics GROUP BY <dimension>, as the metric view runs it."""
    if by is None:
        return {None: con.execute(f"SELECT {MEASURES[name]} FROM quote_fact").fetchone()[0]}
    return dict(con.execute(f"SELECT {DIMS[by]}, {MEASURES[name]} FROM quote_fact GROUP BY 1").fetchall())


def q(i, outcome, region, rep, state="ISSUED", price=8.60, approval=False, hours=10):
    req = datetime(2026, 7, 1 + i % 20, 8)
    return {"quote_id": f"Q{i}", "region": region, "rep_id": rep, "account_name": "Lakeside Regional Clinic",
            "product_family": "GLOVE-A", "material": "nitrile", "state": state, "outcome": outcome,
            "quantity": 100, "list_price": 8.60, "unit_price": price, "approval_needed": approval,
            "requested_at": req, "issued_at": None if state == "CANCELLED" else req.replace(hour=8 + hours)}


# 10 decided quotes: 4 won, 4 lost, 2 expired. Midwest: alice 3/3 won, bob 0/2; Northeast: 1 of 5.
TEN = ([q(i, "WON", "Midwest", "alice") for i in range(3)] + [q(3, "LOST", "Midwest", "bob"),
                                                               q(4, "EXPIRED", "Midwest", "bob")]
       + [q(5, "WON", "Northeast", "carol"), q(6, "LOST", "Northeast", "carol"), q(7, "LOST", "Northeast", "carol"),
          q(8, "LOST", "Northeast", "dan"), q(9, "EXPIRED", "Northeast", "dan")])


def test_known_answer_win_rate_is_exactly_0_40():
    assert measure(db(TEN), "win_rate") == {None: pytest.approx(0.40)}


def test_cancelled_and_open_quotes_change_neither_win_rate_nor_count_cancelled():
    rows = TEN + [q(10, None, "Midwest", "alice", state="CANCELLED"), q(11, None, "Midwest", "alice", state="PRICED")]
    con = db(rows)
    assert measure(con, "win_rate") == {None: pytest.approx(0.40)}
    assert measure(con, "quotes") == {None: 11}


def test_a_copied_definition_without_expired_gives_0_50_and_is_wrong():
    con = db(TEN)
    wrong = con.execute("SELECT COUNT_IF(outcome = 'WON') / COUNT_IF(outcome IN ('WON', 'LOST')) "
                        "FROM quote_fact").fetchone()[0]
    assert wrong == pytest.approx(0.50) and wrong != measure(con, "win_rate")[None]


def test_win_rate_by_region_is_computed_from_rows_not_averaged_over_reps():
    con = db(TEN)
    by_region = measure(con, "win_rate", "region")
    assert by_region == {"Midwest": pytest.approx(3 / 5), "Northeast": pytest.approx(1 / 5)}
    by_rep = measure(con, "win_rate", "rep")
    avg_of_avgs = (by_rep["alice"] + by_rep["bob"]) / 2          # 0.5: what a dashboard must never show
    assert avg_of_avgs != pytest.approx(by_region["Midwest"])


def test_discount_turnaround_and_approval_rate():
    rows = [q(0, "WON", "Midwest", "a", price=8.60, hours=2), q(1, "LOST", "Midwest", "a", price=7.74, approval=True,
                                                                hours=6),
            q(2, None, "Midwest", "a", state="CANCELLED", price=1.00)]
    con = db(rows)
    assert measure(con, "avg_discount_pct")[None] == pytest.approx(0.05)     # (0 + 0.86*100) / (8.60*200)
    assert measure(con, "quote_turnaround_h")[None] == pytest.approx(4.0)
    assert measure(con, "approval_rate")[None] == pytest.approx(0.5)


def test_the_metric_views_define_the_six_measures_and_the_dimensions():
    order = spec("order_metrics.sql")
    assert {"quotes", "win_rate", "avg_discount_pct", "quote_turnaround_h", "approval_rate"} <= set(MEASURES)
    assert "margin_pct" in {m["name"] for m in order["measures"]}
    assert {"region", "rep", "account", "product_family", "material", "month", "state"} <= set(DIMS)
    assert QUOTE["source"] == "cat.quote_ai_sales.quote_fact"
    assert all(m.get("comment") for m in QUOTE["measures"] + order["measures"])


def test_the_synthetic_history_has_every_outcome_and_lakeside_orders():
    facts = sales.quote_facts()
    outcomes = {f["outcome"] for f in facts}
    assert {"WON", "LOST", "EXPIRED", None} <= outcomes
    assert {f["state"] for f in facts} >= {"ISSUED", "EXPIRED", "CANCELLED"}
    assert all(f["issued_at"] is None or f["issued_at"] >= f["requested_at"] for f in facts)
    orders = sales.order_lines(facts)
    assert len(orders) == sum(f["outcome"] == "WON" for f in facts)
    assert any(o["account_id"] == "ACC-001" and o["order_date"].year == 2026 for o in orders)
    rate = measure(db(facts), "win_rate")[None]
    assert 0.2 < rate < 0.8


# ---------------------------------------------------------------- the dashboard lint (CI)


def test_the_dashboard_queries_only_metric_views_and_hard_codes_no_catalog():
    d = dashboard.load()
    assert dashboard.lint(d) == []
    text = json.dumps(d)
    assert "dbxarchitectlab" not in text and "quote_ai_sales." not in text


def test_the_lint_fails_a_dataset_that_re_implements_win_rate():
    d = dashboard.load()
    d["datasets"].append({"name": "my_win_rate", "queryLines": [
        "SELECT region, COUNT_IF(outcome = 'WON') / COUNT_IF(outcome IN ('WON','LOST')) FROM quote_fact "
        "GROUP BY region"]})
    errors = dashboard.lint(d)
    assert any("my_win_rate" in e and "metric views" in e for e in errors)
    assert any("by hand" in e for e in errors)


def test_the_dashboard_resource_points_at_the_file_with_viewer_credentials():
    res = yaml.safe_load((ROOT / "resources/quote_ai/quote_ai_sales_overview.dashboard.yml").read_text())
    dash = res["resources"]["dashboards"]["quote_ai_sales_overview"]
    assert (ROOT / "resources/quote_ai" / dash["file_path"]).resolve() == dashboard.PATH
    assert dash["embed_credentials"] is False and dash["display_name"] == dashboard.DISPLAY_NAME
    assert dash["dataset_schema"] == "${resources.schemas.quote_ai_sales.name}"


def test_rep_id_for_the_signed_in_user():
    assert dashboard.rep_id_for("rep.alice@dbxlab.example") == "rep.alice"
