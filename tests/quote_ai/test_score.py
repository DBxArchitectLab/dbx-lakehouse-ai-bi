"""Register, serve and monitor (AI-14): scoring uses @champion and never a run ID or a version; one non-null score per
open quote; the chance never rises with the price; promotion only for a challenger that wins; drift is caught; the
endpoint, jobs and app resource are wired that way in the bundle. The Win model suite, continued. Synthetic data."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from quote_ai.mock import catalog, generate_dbxlab, sales
from quote_ai.ml import features as F
from quote_ai.ml import monitor, quick, register, score
from quote_ai.storage import sql_files

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "resources" / "quote_ai"
NAMES = sql_files.names_for("cat", {"quote_ai_ml": "dev_me_quote_ai_ml"})
UTC = timezone.utc
LAKESIDE_LINES = [{"line_no": i + 1, "size": s, "quantity": n, "sku_id": f"GLOVE-A-{s}", "unit_price": 8.60,
                   "list_price": 8.60} for i, (s, n) in enumerate([("S", 60), ("M", 140), ("L", 140), ("XL", 60)])]


def res(file):
    return yaml.safe_load((RES / file).read_text(encoding="utf-8"))["resources"]


# ------------------------------------------------------------------ the model is addressed by alias

def test_scoring_uses_the_champion_alias_never_a_run_id_or_a_version():
    assert score.scoring_uri(NAMES) == "models:/cat.dev_me_quote_ai_ml.win_model@champion"
    for bad in ("runs:/0a1b2c/model", "models:/cat.quote_ai_ml.win_model/3"):
        with pytest.raises(score.ScoringError):
            score.assert_alias_uri(bad)
    with pytest.raises(ValueError):
        register.alias_uri("cat.s.win_model", "latest")


def test_the_endpoint_serves_a_variable_set_from_the_alias_never_a_literal_version():
    (ep,) = res("quote_ai_win_model.serving.yml")["model_serving_endpoints"].values()
    (entity,) = ep["config"]["served_entities"]
    assert entity["entity_version"] == "${var.quote_ai_win_model_version}"
    assert entity["entity_name"].endswith(".win_model") and entity["scale_to_zero_enabled"] is True
    assert ep["name"] == "quote_ai_win_model_${var.quote_ai_target}" and entity["workload_size"] == "Small"
    cd = (ROOT / ".github/workflows/cd.yml").read_text(encoding="utf-8")
    assert cd.count("--var quote_ai_win_model_version=$WIN_MODEL_VERSION") == 2 and "--champion-version" in cd


def test_the_ml_jobs_are_tagged_and_run_the_ml_job():
    for file, key in (("quote_ai_ml_features.job.yml", "quote_ai_ml_features"), ("quote_ai_ml_train.job.yml",
                      "quote_ai_train"), ("quote_ai_ml_register.job.yml", "quote_ai_ml_register"),
                      ("quote_ai_ml_score.job.yml", "quote_ai_ml_score")):
        job = res(file)["jobs"][key]
        assert job["name"] == key and job["tags"]["app"] == "quote_ai"
        for t in job["tasks"]:
            assert t["spark_python_task"]["python_file"] == "../../src/quote_ai/jobs/ml_job.py"
            assert "${resources.schemas.quote_ai_ml.name}" in " ".join(t["spark_python_task"]["parameters"])
    assert [t["task_key"] for t in res("quote_ai_ml_features.job.yml")["jobs"]["quote_ai_ml_features"]["tasks"]] == \
        ["setup", "seed", "features", "training_set"]


def test_the_app_queries_the_endpoint_through_an_app_resource():
    (app,) = res("quote_ai_app.app.yml")["apps"].values()
    (r,) = [r for r in app["resources"] if r["name"] == "win-model"]
    assert r["serving_endpoint"] == {"name": "${resources.model_serving_endpoints.quote_ai_win_model.name}",
                                     "permission": "CAN_QUERY"}
    spec = yaml.safe_load((ROOT / "src/quote_ai/app/app.yaml").read_text(encoding="utf-8"))
    assert {"name": "QUOTE_AI_WIN_ENDPOINT", "valueFrom": "win-model"} in spec["env"]


# ------------------------------------------------------------------ promotion

def test_the_register_job_refuses_to_promote_a_version_that_does_not_beat_the_champion():
    assert register.should_promote(0.68, 0.70) == (False, register.should_promote(0.68, 0.70)[1])
    assert not register.should_promote(0.70, 0.70)[0]                # a tie keeps the champion
    ok, reason = register.should_promote(0.72, 0.70)
    assert ok and "beats champion" in reason
    ok, reason = register.should_promote(0.69, None)
    assert ok and "no champion yet" in reason


def test_the_latest_month_is_where_a_challenger_proves_itself():
    t = datetime(2026, 9, 30, tzinfo=UTC)
    rows = [{"quote_id": q, "sent_at": t - timedelta(days=d)} for q, d in (("a", 0), ("b", 20), ("c", 31), ("d", 90))]
    assert [r["quote_id"] for r in register.latest_month(rows)] == ["a", "b"]
    assert register.latest_month([]) == []


def test_execute_on_the_model_only_for_named_identities():
    assert register.grant_statements("cat.s.win_model", ["app-sp", None]) == \
        ["GRANT EXECUTE ON MODEL cat.s.win_model TO `app-sp`"]


# ------------------------------------------------------------------ scoring rows and the assertions

def test_lakeside_what_if_rows_change_only_the_price():
    rows = score.what_if_rows("Q1", "Lakeside Regional Clinic", LAKESIDE_LINES, [7.60, 7.90, 8.20])
    assert {(r["account_id"], r["sku_id"], r["quantity_boxes"], r["list_price"]) for r in rows} == \
        {("ACC-001", "GLOVE-A-M", 400, 8.60)}
    assert [r["price_vs_list_pct"] for r in rows] == [-11.63, -8.14, -4.65]
    assert [r["needs_approval"] for r in rows] == [True, True, False]       # above the rep's 5% only below $8.17
    assert set(score.request_record(rows[0])) == {"account_id", "sku_id", *F.CONTEXT_FEATURES}
    with pytest.raises(score.ScoringError):
        score.what_if_rows("Q2", "Lakeside Regional Clinic", [{"quantity": 10, "sku_id": None}], [8.0])
    with pytest.raises(score.ScoringError):
        score.account_id("Nobody Clinic")


def test_a_prediction_is_read_as_the_chance_of_winning():
    assert score.win_probability([0.36, 0.64]) == 0.64 and score.win_probability(0.58) == 0.58
    assert score.win_probability({"0": 0.51, "1": 0.49}) == 0.49
    for bad in (float("nan"), 1.2, [0.5, -0.1]):
        with pytest.raises(score.ScoringError):
            score.win_probability(bad)


def test_batch_output_has_one_non_null_row_per_open_quote():
    assert score.check_scores(["Q1", "Q2"], [{"quote_id": "Q1", "prediction": 0.6},
                                              {"quote_id": "Q2", "prediction": 0.4}]) == []
    problems = score.check_scores(["Q1", "Q2", "Q3"], [{"quote_id": "Q1", "prediction": None},
                                                        {"quote_id": "Q2", "prediction": 0.4},
                                                        {"quote_id": "Q2", "prediction": 0.4},
                                                        {"quote_id": "Q9", "prediction": 0.4}])
    assert problems == ["Q1: no prediction", "Q3: missing from the scores", "Q2: scored 2 times",
                        "Q9: not an open quote"]


def test_for_the_same_quote_the_chance_never_rises_with_the_price():
    assert score.check_monotonic([(7.60, 0.64), (7.90, 0.58), (8.20, 0.49)]) == []
    assert score.check_monotonic([(8.20, 0.66), (7.60, 0.64)]) == ["$8.20 scores 66%, above $7.60 at 64%"]


def test_win_chances_use_one_call_for_every_price():
    calls = []

    def endpoint(records):
        calls.append(records)
        return [[1 - p, p] for p in (0.64, 0.58, 0.49)]
    rows = score.what_if_rows("Q1", "ACC-001", LAKESIDE_LINES, [7.60, 7.90, 8.20])
    out = score.win_chances(rows, endpoint)
    assert len(calls) == 1 and len(calls[0]) == 3
    assert [(c["price"], c["win_chance"]) for c in out] == [(7.60, 0.64), (7.90, 0.58), (8.20, 0.49)]
    with pytest.raises(score.ScoringError):
        score.win_chances(rows, lambda records: [0.5])


# ------------------------------------------------------------------ a stand-in endpoint on the simulation, and drift

@pytest.fixture(scope="module")
def sim():
    facts = sales.quote_facts()
    gen = generate_dbxlab.generate()
    built = F.build(facts, sales.order_lines(facts), gen["customer_price_history"], gen["inventory_history"],
                    catalog.skus())
    train, _, _ = F.time_split(built["training"])
    cols = ["price_vs_customer_current_pct", "lead_time_days", "stock_cover_days"]
    model = quick.QuickModel(cols, epochs=150).fit(train[::3], [r["won"] for r in train[::3]])
    return {"facts": facts, "gen": gen, "built": built, "model": model}


def endpoint_for(sim, prices):
    """What the serving endpoint does: look up the latest account and SKU features, compute the on-demand feature,
    score. A stand-in for the champion, for the route's and the monitor's logic."""
    acc = F.AsOf(F.account_feature_rows(sim["facts"], [], prices, generate_dbxlab.TODAY.date(),
                                        generate_dbxlab.TODAY.date()), "account_id", "as_of")
    sku = F.AsOf(sim["built"]["sku_features"], "sku_id", "as_of")

    def predict(records):
        rows = []
        for r in records:
            row = dict(r) | dict(acc.latest(r["account_id"])) | dict(sku.latest(r["sku_id"]))
            row["price_vs_customer_current_pct"] = F.pct_diff(r["unit_price"], row["customer_current_price"])
            rows.append(row)
        return [[1 - p, p] for p in sim["model"].predict_proba(rows)]
    return predict


def test_lakeside_at_three_prices_the_chance_falls_as_the_price_rises(sim):
    rows = score.what_if_rows("Q1", "Lakeside Regional Clinic", LAKESIDE_LINES, [7.60, 7.90, 8.20])
    out = score.win_chances(rows, endpoint_for(sim, sim["gen"]["customer_price_history"]))
    chances = [c["win_chance"] for c in out]
    assert chances[0] > chances[1] > chances[2] and score.check_monotonic([(c["price"], c["win_chance"])
                                                                            for c in out]) == []


def test_injected_drift_is_caught_in_the_inputs_and_the_predictions(sim):
    """The drift demo: a month where customers' current prices fall. Last night's scores are the reference."""
    open_quotes = [f for f in sim["facts"] if f["outcome"] is None and f["state"] != "CANCELLED"]
    rows = [{"quote_id": f["quote_id"], "account_id": f["account_id"], "sku_id": f["sku_id"]}
            | F.request_features(f["unit_price"], f["list_price"], f["quantity"]) for f in open_quotes]

    def scored(prices):
        predict = endpoint_for(sim, prices)
        acc = F.AsOf(prices, "account_id", "observed_at")
        out = []
        for r, p in zip(rows, predict([score.request_record(r) for r in rows])):
            cur = acc.latest(r["account_id"])["current_price"]
            out.append({"quote_id": r["quote_id"], "prediction": score.win_probability(p),
                        "price_vs_customer_current_pct": F.pct_diff(r["unit_price"], cur)})
        return out
    before = scored(sim["gen"]["customer_price_history"])
    after = scored(sim["gen"]["customer_price_history"] + generate_dbxlab.drift_month())
    assert score.check_scores([r["quote_id"] for r in rows], after) == []
    report = monitor.drift(before, after, ["price_vs_customer_current_pct", "prediction"])
    assert report["price_vs_customer_current_pct"]["drift"] and report["prediction"]["drift"]
    assert monitor.drift(before, before, ["prediction"])["prediction"]["drift"] is False
    assert sum(a["prediction"] for a in after) < sum(b["prediction"] for b in before)


def test_psi_known_answers():
    same = [i / 100 for i in range(100)]
    assert monitor.psi(same, same) == pytest.approx(0.0)
    assert monitor.psi(same, [x + 0.5 for x in same]) > monitor.DRIFT_PSI
    with pytest.raises(ValueError):
        monitor.psi([], [1.0])
