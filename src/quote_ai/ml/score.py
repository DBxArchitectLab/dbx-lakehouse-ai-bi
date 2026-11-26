"""Scoring the win model (AI-14): nightly for every open quote, and on demand for what-if prices.

  batch      every open quote, scored by quote_ai_ml.win_model@champion with Feature Engineering's score_batch
             (account and SKU features looked up as of now), into quote_ai_ml.open_quote_scores. Open quotes: the
             app's live quotes (quote_ai_sales.quote) in SHORTLISTED or PRICED, and the synthetic open quotes of the
             sales history (quote_fact, AI-10)
  what-if    one row per candidate price, built by quote_ai.ml.features.request_features, sent in one request to the
             serving endpoint quote_ai_win_model_<target> (the app now; the agent's win_probability tool in AI-18).
             Only the quote's own values go in the request; the endpoint looks up the account and SKU features from
             the same feature tables as training

The assertions every scoring run must pass (check_*): the model is addressed by alias, never by version or run ID;
one non-null row per open quote; for the same quote the chance never rises with the price; the label is shown.
The win chance informs the rep. It never sets the price: that comes from the price book and the policy
(quote_ai.rules.pricing). It is labelled everywhere as a simulation on demo data.
"""
from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence

from quote_ai.ingest.synthetic import CUSTOMERS     # AI-02's synthetic accounts (read, never changed here)
from quote_ai.ml import features as F
from quote_ai.ml import register

LABEL = "simulation, demo data"
NOTE = "The win chance informs you; the price comes from the price book and the policy."
OPEN_STATES = ("SHORTLISTED", "PRICED")
_ALIAS_URI = re.compile(r"^models:/[A-Za-z0-9_.]+@[a-z_]+$")


class ScoringError(AssertionError):
    pass


def scoring_uri(names: Mapping[str, str]) -> str:
    """The only model URI scoring uses: @champion."""
    return assert_alias_uri(register.alias_uri(register.model_name(names), register.CHAMPION))


def assert_alias_uri(uri: str) -> str:
    """Refuse runs:/<id>/model and models:/<name>/<version>: production follows the alias."""
    if not _ALIAS_URI.match(uri):
        raise ScoringError(f"{uri}: score with an alias (models:/<name>@champion), never a run ID or a version")
    return uri


def account_id(account: str) -> str:
    """An app quote names its account (Lakeside Regional Clinic); the features are keyed by account ID (ACC-001)."""
    by_name = {name.lower(): aid for aid, name, _, _ in CUSTOMERS}
    if account in {aid for aid, *_ in CUSTOMERS}:
        return account
    try:
        return by_name[account.strip().lower()]
    except KeyError:
        raise ScoringError(f"unknown account {account!r}") from None


def quote_row(quote_id: str, account: str, lines: Sequence[Mapping], unit_price: float | None = None) -> dict:
    """One scoring row per quote: the SKU of its biggest line (sizes of one product share stock and lead time in
    DBxLab's data), all its boxes, and its price (or a proposed one)."""
    priced = [l for l in lines if l.get("sku_id") and l.get("list_price") is not None]
    if not priced:
        raise ScoringError(f"{quote_id}: no line with a SKU and a list price yet (shortlist and price it first)")
    main = max(priced, key=lambda l: l["quantity"])
    price = unit_price if unit_price is not None else main.get("unit_price") or main["list_price"]
    return {"quote_id": quote_id, "account_id": account_id(account), "sku_id": main["sku_id"]} | \
        F.request_features(float(price), float(main["list_price"]), sum(int(l["quantity"]) for l in priced))


def what_if_rows(quote_id: str, account: str, lines: Sequence[Mapping], prices: Sequence[float]) -> list[dict]:
    """The same quote at several prices: one row each, everything but the price unchanged."""
    return [quote_row(quote_id, account, lines, p) for p in prices]


def request_record(row: Mapping) -> dict:
    """What the endpoint receives: the lookup keys and the quote's own values (no quote_id, no looked-up feature)."""
    return {k: row[k] for k in ("account_id", "sku_id", *F.CONTEXT_FEATURES)}


def win_probability(prediction) -> float:
    """P(won) from one prediction: the model is logged with predict_proba, so a prediction is [P(lost), P(won)];
    a bare probability is accepted too."""
    if isinstance(prediction, Mapping):
        prediction = prediction.get("1", prediction.get(1, prediction.get("prediction")))
    if isinstance(prediction, (list, tuple)):
        prediction = prediction[-1]
    p = float(prediction)
    if math.isnan(p) or not 0.0 <= p <= 1.0:
        raise ScoringError(f"not a probability: {prediction!r}")
    return p


# ------------------------------------------------------------------ the assertions on every scoring run

def check_scores(open_quote_ids: Iterable[str], scores: Iterable[Mapping]) -> list[str]:
    """One non-null row per open quote, nothing else."""
    expected = list(open_quote_ids)
    seen: dict[str, int] = {}
    problems = []
    for s in scores:
        seen[s["quote_id"]] = seen.get(s["quote_id"], 0) + 1
        p = s.get("prediction")
        if p is None or (isinstance(p, float) and math.isnan(p)):
            problems.append(f"{s['quote_id']}: no prediction")
    problems += [f"{q}: missing from the scores" for q in expected if q not in seen]
    problems += [f"{q}: scored {n} times" for q, n in seen.items() if n > 1]
    problems += [f"{q}: not an open quote" for q in seen if q not in set(expected)]
    return problems


def check_monotonic(points: Sequence[tuple[float, float]], tolerance: float = 1e-9) -> list[str]:
    """For one quote: the chance never rises with the price."""
    ordered = sorted(points)
    return [f"${b[0]:.2f} scores {b[1]:.0%}, above ${a[0]:.2f} at {a[1]:.0%}"
            for a, b in zip(ordered, ordered[1:]) if b[1] > a[1] + tolerance]


def win_chances(rows: Sequence[Mapping], predict: Callable[[list[dict]], list]) -> list[dict]:
    """Score what-if rows in ONE call (the endpoint, or a stand-in in tests) and check them."""
    preds = predict([request_record(r) for r in rows])
    if len(preds) != len(rows):
        raise ScoringError(f"{len(rows)} rows sent, {len(preds)} predictions back")
    out = [{"price": r["unit_price"], "win_chance": round(win_probability(p), 4),
            "price_vs_list_pct": r["price_vs_list_pct"], "needs_approval": r["needs_approval"]}
           for r, p in zip(rows, preds)]
    return out


# ------------------------------------------------------------------ Databricks

def query_endpoint(endpoint: str) -> Callable[[list[dict]], list]:
    """The serving endpoint as a function: records in, predictions out, one request. Verify before recording:
    serving_endpoints.query(name, dataframe_records=...) and the shape of .predictions for predict_proba."""
    def predict(records: list[dict]) -> list:
        from databricks.sdk import WorkspaceClient
        return list(WorkspaceClient().serving_endpoints.query(name=endpoint, dataframe_records=records).predictions)
    return predict


def open_quotes(spark, names: Mapping[str, str]) -> list[dict]:
    """Open quotes from both sources, as scoring rows (+ source)."""
    sales = names["sales"]
    live = [r.asDict() for r in spark.sql(
        f"SELECT q.quote_id, q.account, l.sku_id, CAST(l.unit_price AS DOUBLE) AS unit_price, "
        f"CAST(l.list_price AS DOUBLE) AS list_price, l.quantity FROM {sales}.quote q "
        f"JOIN {sales}.quote_line l ON l.quote_id = q.quote_id AND l.version = q.version "
        f"WHERE q.state IN ({', '.join(repr(s) for s in OPEN_STATES)})").collect()]
    by_quote: dict[str, list] = {}
    for r in live:
        by_quote.setdefault((r["quote_id"], r["account"]), []).append(r)
    out = [quote_row(q, a, lines) | {"source": "app"} for (q, a), lines in by_quote.items()]
    history = spark.sql(
        f"SELECT quote_id, account_id, sku_id, CAST(unit_price AS DOUBLE) AS unit_price, "
        f"CAST(list_price AS DOUBLE) AS list_price, quantity FROM {sales}.quote_fact "
        f"WHERE outcome IS NULL AND state <> 'CANCELLED'").collect()
    out += [{"quote_id": r.quote_id, "account_id": r.account_id, "sku_id": r.sku_id, "source": "sales_history"}
            | F.request_features(r.unit_price, r.list_price, r.quantity) for r in history]
    return out


def score_batch(spark, fe, names: Mapping[str, str], rows: list[dict], model_version: str):
    """fe.score_batch with the champion alias; features as of now (sent_at = the scoring time). Returns the rows to
    append to open_quote_scores, with the looked-up inputs the monitor watches. Verify before recording: score_batch
    on a model logged with timestamp lookups, and its output columns."""
    from pyspark.sql import functions as fn
    df = spark.createDataFrame(rows).withColumn("sent_at", fn.current_timestamp())
    scored = fe.score_batch(model_uri=scoring_uri(names), df=df)
    inputs = F.create_training_set(fe, df, names, label=None, exclude=F.RAW_INPUTS).load_df()
    keep = ["quote_id", "price_vs_customer_current_pct", "lead_time_days", "stock_cover_days", "win_rate_12m"]
    out = scored.select("quote_id", "source", "account_id", "sku_id", "unit_price", "price_vs_list_pct",
                        "needs_approval", "prediction").join(inputs.select(*keep), "quote_id", "left")
    return [r.asDict() | {"prediction": win_probability(r["prediction"]), "model_version": model_version}
            for r in out.collect()]
