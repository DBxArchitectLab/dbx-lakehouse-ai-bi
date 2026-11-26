"""From quote history to ML features (AI-12): the label matches the business metric, every lookup is point in time,
the excluded columns never reach the model, the splits don't overlap, and a leaky feature that looks like success is
refused. The Win model suite starts here (AI-13 adds the metrics). All synthetic demo data, no workspace."""
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from quote_ai.mock import catalog, generate_dbxlab, sales
from quote_ai.ml import features as F
from quote_ai.ml import quick

ROOT = Path(__file__).resolve().parents[2]
UTC = timezone.utc
LAKESIDE = "ACC-001"
LAKESIDE_MARCH_10 = "QH-02875"     # a Lakeside quote sent on 2026-03-10


@pytest.fixture(scope="module")
def data():
    facts = sales.quote_facts()
    generated = generate_dbxlab.generate()
    today = [{"sku_id": s["sku_id"], "as_of": generate_dbxlab.TODAY, "available": 1000, "lead_time_days": 7}
             for s in catalog.skus()]                       # AI-02's ingested inventory, today
    built = F.build(facts, sales.order_lines(facts), generated["customer_price_history"],
                    generated["inventory_history"] + today, catalog.skus())
    return {"facts": facts, **generated, **built}


def row(rows, quote_id):
    (r,) = [r for r in rows if r["quote_id"] == quote_id]
    return r


# ------------------------------------------------------------------ the data and the label

def test_the_generator_is_deterministic_two_years_and_documents_the_leak():
    h = generate_dbxlab.quote_history()
    assert h == generate_dbxlab.quote_history()
    assert h[0]["sent_at"].date().isoformat() < "2024-10-15" and h[-1]["sent_at"].date().isoformat() >= "2026-09-25"
    assert {r["outcome"] for r in h} == {"WON", "LOST", "EXPIRED"} and all(r["demo_data"] for r in h)
    assert all((r["final_negotiated_price"] is not None) == (r["outcome"] == "WON") for r in h)
    assert all(r["decided_at"] > r["sent_at"] for r in h)


def test_expired_quotes_are_spread_over_the_history_so_the_label_has_no_step():
    expired = sorted(f["issued_at"] for f in sales.quote_facts() if f["outcome"] == "EXPIRED")
    assert expired[0].year == 2024 and expired[-1] >= datetime(2026, 8, 1, tzinfo=UTC)


def test_the_label_is_the_win_rate_metric(data):
    text = (ROOT / "src/quote_ai/semantic/quote_metrics.sql").read_text(encoding="utf-8")
    (decided,) = re.findall(r"COUNT_IF\(outcome IN \(([^)]*)\)\)", text)
    assert tuple(s.strip(" '") for s in decided.split(",")) == F.DECIDED
    labels = {l["quote_id"]: l["won"] for l in data["quote_labels"]}
    for f in data["facts"]:
        if f["outcome"] in ("WON", "LOST", "EXPIRED"):
            assert labels[f["quote_id"]] == int(f["outcome"] == "WON")
        else:
            assert f["quote_id"] not in labels           # cancelled and open quotes are left out


def test_request_features_match_what_the_sales_history_recorded(data):
    for f in data["facts"]:
        r = F.request_features(f["unit_price"], f["list_price"], f["quantity"])
        assert r["needs_approval"] == f["approval_needed"], f["quote_id"]
    assert F.request_features(8.00, 8.60, 400)["price_vs_list_pct"] == -6.98


def test_the_price_function_in_unity_catalog_is_the_same_expression_as_pct_diff():
    sql = (ROOT / "src/quote_ai/ml/feature_tables.sql").read_text(encoding="utf-8")
    body = sql.split("AS $$")[1].split("$$")[0]
    ns: dict = {}
    exec("def f(unit_price, customer_current_price):\n" + "\n".join("    " + l for l in body.strip().splitlines()), ns)
    for price, ref in [(7.60, 8.00), (8.20, 8.00), (8.0, None), (None, 8.0), (8.6, 7.93)]:
        assert ns["f"](price, ref) == F.pct_diff(price, ref)
    assert F.pct_diff(7.60, 8.00) == -5.0


# ------------------------------------------------------------------ point in time

def test_a_feature_value_created_after_sent_at_never_appears_in_the_training_row():
    sent = datetime(2026, 3, 10, 12, tzinfo=UTC)
    labels = [{"quote_id": "Q1", "account_id": "A", "sku_id": "S", "sent_at": sent, "won": 1}]
    context = [{"quote_id": "Q1", "sent_at": sent} | F.request_features(7.60, 8.60, 400)]
    accounts = [{"account_id": "A", "as_of": sent - timedelta(days=1), "win_rate_12m": 0.40,
                 "customer_current_price": 8.00},
                {"account_id": "A", "as_of": sent + timedelta(minutes=1), "win_rate_12m": 0.90,
                 "customer_current_price": 7.00}]
    skus = [{"sku_id": "S", "as_of": sent, "lead_time_days": 6, "stock_cover_days": 20.0},   # at sent_at: allowed
            {"sku_id": "S", "as_of": sent + timedelta(hours=1), "lead_time_days": 30, "stock_cover_days": 0.0}]
    (r,) = F.training_rows(labels, context, accounts, skus)
    assert (r["win_rate_12m"], r["customer_current_price"], r["lead_time_days"]) == (0.40, 8.00, 6)
    assert r["price_vs_customer_current_pct"] == -5.0
    assert F.check_training_set([r]) == []
    (leaky,) = F.plain_join_rows(labels, context, accounts, skus, [])
    assert leaky["win_rate_12m"] == 0.90 and leaky["lead_time_days"] == 30
    assert any("after sent_at" in p for p in F.check_training_set([leaky]))


def test_win_rate_counts_only_quotes_decided_before_as_of():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    quotes = [{"account_id": "A", "outcome": "WON", "decided_at": t0 + timedelta(days=1)},
              {"account_id": "A", "outcome": "LOST", "decided_at": t0 + timedelta(days=3)},
              {"account_id": "A", "outcome": "CANCELLED", "decided_at": t0 + timedelta(days=2)}]
    rows = F.account_feature_rows(quotes, [], [], t0.date(), (t0 + timedelta(days=4)).date())
    by_day = {r["as_of"].day: r["win_rate_12m"] for r in rows}
    assert by_day == {1: None, 2: None, 3: 1.0, 4: 1.0, 5: 0.5}


def test_lakeside_march_10_passes_every_check_and_sits_in_train(data):
    r = row(data["training"], LAKESIDE_MARCH_10)
    assert r["account_id"] == LAKESIDE and r["sent_at"].date().isoformat() == "2026-03-10"
    assert r["_account_as_of"] <= r["sent_at"] and r["_sku_as_of"] <= r["sent_at"]
    assert not set(F.EXCLUDED) & set(r) and F.split_of(r["sent_at"]) == "train"
    acc = F.AsOf(data["account_features"], "account_id", "as_of")
    assert r["win_rate_12m"] == acc.get(LAKESIDE, r["sent_at"])["win_rate_12m"]
    h = row(data["quote_history"], LAKESIDE_MARCH_10)
    assert r["lead_time_days"] == h["lead_time_days"] and r["customer_current_price"] == h["customer_current_price"]


def test_the_same_quote_built_the_easy_way_is_refused(data):
    leaky = row(F.plain_join_rows(data["quote_labels"], data["quote_context_features"], data["account_features"],
                                  data["sku_features"], data["quote_history"]), LAKESIDE_MARCH_10)
    honest = row(data["training"], LAKESIDE_MARCH_10)
    today = F.AsOf(data["account_features"], "account_id", "as_of").latest(LAKESIDE)
    assert leaky["win_rate_12m"] == today["win_rate_12m"] != honest["win_rate_12m"]
    problems = F.check_training_set([leaky])
    assert any("final_negotiated_price" in p for p in problems) and any("after sent_at" in p for p in problems)
    with pytest.raises(F.LeakageError):
        F.assert_training_set([leaky])


def test_point_in_time_two_lakeside_dates_two_values_a_plain_join_gives_one(data):
    lakeside = [r for r in data["training"] if r["account_id"] == LAKESIDE]
    a, b = lakeside[len(lakeside) // 3], lakeside[2 * len(lakeside) // 3]
    assert a["win_rate_12m"] != b["win_rate_12m"]
    plain = {r["quote_id"]: r for r in F.plain_join_rows(lakeside, data["quote_context_features"],
                                                         data["account_features"], data["sku_features"], [])}
    assert plain[a["quote_id"]]["win_rate_12m"] == plain[b["quote_id"]]["win_rate_12m"]


def test_chemo_verified_counts_only_verified_evidence_from_its_verification_on():
    t = datetime(2026, 5, 1, tzinfo=UTC)
    inventory = [{"sku_id": "GLOVE-A-M", "as_of": t, "available": 400, "lead_time_days": 7},
                 {"sku_id": "GLOVE-A-M", "as_of": t + timedelta(days=10), "available": 400, "lead_time_days": 7}]
    evidence = [{"product_id": "GLOVE-A", "attribute_name": "chemo_tested", "attribute_value": "true",
                 "verification_status": "unverified", "verified_at": None},
                {"product_id": "GLOVE-A", "attribute_name": "chemo_tested", "attribute_value": "true",
                 "verification_status": "verified", "verified_at": t + timedelta(days=5)}]
    skus = [s for s in catalog.skus() if s["sku_id"] == "GLOVE-A-M"]
    rows = F.sku_feature_rows(inventory, skus, evidence)
    assert [(r["as_of"] - t).days for r in rows] == [0, 5, 10]
    assert [r["chemo_verified"] for r in rows] == [False, True, True]
    assert rows[0]["stock_cover_days"] == 10.0 and rows[0]["material"] == "nitrile"


# ------------------------------------------------------------------ the training set and the split

def test_every_training_row_is_clean_and_has_exactly_the_model_inputs(data):
    rows = data["training"]
    assert len(rows) == len(data["quote_labels"]) and F.check_training_set(rows) == []
    assert all(set(F.model_input(r)) == set(F.FEATURES) for r in rows)
    assert not set(F.EXCLUDED) & set(F.FEATURES)


def test_splits_do_not_overlap_in_time(data):
    train, valid, test = F.time_split(data["training"])
    assert max(r["sent_at"] for r in train).date() <= F.TRAIN_END < min(r["sent_at"] for r in valid).date()
    assert max(r["sent_at"] for r in valid).date() <= F.VALID_END < min(r["sent_at"] for r in test).date()
    assert len(test) > 300 and len(valid) > 200       # enough held-out quotes to measure AI-13's rule above the noise
    assert F.check_training_set(train + valid + test, {"train": train, "valid": valid, "test": test}) == []


def test_a_random_split_is_caught_mixing_the_future_into_training(data):
    a, b = quick.random_split(data["training"])
    problems = F.check_training_set([], {"train": a, "test": b})
    assert any("after test starts" in p for p in problems)


def test_compare_finds_a_lookup_that_disagrees():
    pure = [{"quote_id": "Q1", "win_rate_12m": 0.5}, {"quote_id": "Q2", "win_rate_12m": None}]
    assert F.compare(pure, [{"quote_id": "Q1", "win_rate_12m": 0.5}, {"quote_id": "Q2", "win_rate_12m": None}],
                     ["win_rate_12m"]) == []
    assert F.compare(pure, [{"quote_id": "Q1", "win_rate_12m": 0.7}], ["win_rate_12m"]) == [
        "Q1: win_rate_12m 0.5 != 0.7", "Q2: missing from the training set"]


# ------------------------------------------------------------------ the demo: a leak that looks like success

def test_the_leak_looks_like_success_then_the_honest_score(data):
    """A quick model with final_negotiated_price: near perfect on a random split, still suspicious on a time split.
    Without it, an honest score. (Every 4th quote, to keep the test fast.)"""
    cols = list(F.FEATURES)
    sample = F.plain_join_rows(data["quote_labels"][::4], data["quote_context_features"], data["account_features"],
                               data["sku_features"], data["quote_history"])
    a, b = quick.random_split(sample)
    assert quick.score(a, b, cols + ["final_negotiated_price"]) > 0.97
    train, _, test = F.time_split(sample)
    assert quick.score(train, test, cols + ["final_negotiated_price"]) > 0.97
    honest_train, _, honest_test = F.time_split(data["training"][::4])
    assert 0.6 < quick.score(honest_train, honest_test, cols) < 0.8
