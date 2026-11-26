"""Train, track and compare (AI-13): the metrics, the rule baseline, the selection rule written in advance, and the
check only a simulation allows (the winner finds the planted signal). The Win model suite, continued. The sklearn and
MLflow side runs only in the training job; here a dependency-free logistic regression stands in for a candidate, on
the same synthetic training set."""
from pathlib import Path

import pytest

from quote_ai.mock import catalog, generate_dbxlab, sales
from quote_ai.ml import features as F
from quote_ai.ml import metrics, quick
from quote_ai.ml import train as T
from quote_ai.ml.baseline import RuleBaseline, rule

ROOT = Path(__file__).resolve().parents[2]


# ------------------------------------------------------------------ metrics, known answers

def test_auc_known_answers():
    assert metrics.roc_auc([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9]) == 1.0
    assert metrics.roc_auc([0, 0, 1, 1], [0.9, 0.8, 0.2, 0.1]) == 0.0
    assert metrics.roc_auc([0, 1, 0, 1], [0.5, 0.5, 0.5, 0.5]) == 0.5
    assert metrics.roc_auc([0, 1, 1], [0.2, 0.2, 0.9]) == 0.75          # a tie counts half
    with pytest.raises(ValueError):
        metrics.roc_auc([1, 1], [0.3, 0.4])


def test_brier_and_calibration():
    assert metrics.brier([1, 0], [1.0, 0.0]) == 0.0 and metrics.brier([1, 0], [0.5, 0.5]) == 0.25
    y = [1] * 6 + [0] * 4
    assert metrics.calibration_error(y, [0.6] * 10) == pytest.approx(0.0)       # "60%" means 60%
    assert metrics.calibration_error(y, [0.9] * 10) == pytest.approx(0.3)       # too confident
    (b,) = metrics.calibration_bins(y, [0.6] * 10)
    assert (b["n"], b["observed"]) == (10, 0.6)


def test_accuracy_is_not_reported_always_lose_looks_good_and_is_useless():
    y = [1] * 35 + [0] * 65
    m = metrics.evaluate(y, [0.0] * 100, "test")
    assert "test_accuracy" not in m and m["test_auc"] == 0.5 and m["test_recall"] == 0.0


# ------------------------------------------------------------------ the rule baseline

def test_the_rule_baseline_returns_the_expected_predictions():
    rows = [{"price_vs_customer_current_pct": -5.0, "lead_time_days": 6},     # cheaper and in time: rule true
            {"price_vs_customer_current_pct": 0.0, "lead_time_days": 10},     # at the boundary: still true
            {"price_vs_customer_current_pct": 2.5, "lead_time_days": 6},      # dearer
            {"price_vs_customer_current_pct": -5.0, "lead_time_days": 14},    # late
            {"price_vs_customer_current_pct": None, "lead_time_days": 6}]     # unknown is never a match
    assert [rule(r) for r in rows] == [True, True, False, False, False]
    b = RuleBaseline().fit(rows, [1, 1, 0, 0, 1])
    assert b.p_true == pytest.approx(3 / 4) and b.p_false == pytest.approx(2 / 5)    # smoothed: (wins+1)/(n+2)
    assert b.predict(None, rows) == pytest.approx([0.75, 0.75, 0.4, 0.4, 0.4])
    with pytest.raises(RuntimeError):
        RuleBaseline().predict_proba(rows)


# ------------------------------------------------------------------ the selection rule

BASE = T.RunResult("rule", valid_auc=0.62, test_auc=0.63, test_calibration_error=0.03,
                   top_features=["price_vs_customer_current_pct", "lead_time_days"])
GBT = T.RunResult("gbt", valid_auc=0.70, test_auc=0.70, test_calibration_error=0.03,
                  top_features=["price_vs_customer_current_pct", "lead_time_days", "stock_cover_days"], run_id="r-gbt")
LOGREG = T.RunResult("logreg", valid_auc=0.69, test_auc=0.69, test_calibration_error=0.04,
                     top_features=["lead_time_days", "price_vs_customer_current_pct"], run_id="r-lr")
OVERTUNED = T.RunResult("overtuned", valid_auc=0.80, test_auc=0.65, test_calibration_error=0.09,
                        top_features=["glove_colour", "price_vs_customer_current_pct"], run_id="r-ot")


def test_the_selection_rule_picks_the_best_validation_auc_among_those_that_pass():
    assert T.select([BASE, GBT, LOGREG, OVERTUNED], BASE.test_auc) is GBT


def test_the_over_tuned_candidate_with_the_best_validation_score_is_refused():
    assert T.select([OVERTUNED], BASE.test_auc) is None          # test AUC only +0.02, calibration 0.09


def test_the_selection_rule_returns_none_when_nothing_beats_the_baseline():
    close = T.RunResult("gbt", valid_auc=0.75, test_auc=0.67, test_calibration_error=0.02)
    assert T.select([BASE, close], BASE.test_auc) is None
    d = T.decide([BASE, close])
    assert d["status"] == "none" and d["run_id"] is None and "baseline stays" in d["reason"]


def test_the_baseline_itself_never_wins():
    assert T.select([BASE], BASE.test_auc - 1) is None


def test_a_winner_must_find_the_planted_signal():
    assert T.recovers_signal(GBT.top_features) and T.recovers_signal(LOGREG.top_features)
    assert not T.recovers_signal(["glove_colour", "lead_time_days"]) and not T.recovers_signal(["lead_time_days"])
    lucky = T.RunResult("gbt", valid_auc=0.72, test_auc=0.72, test_calibration_error=0.02,
                        top_features=["glove_colour", "lead_time_days"], run_id="r")
    d = T.decide([BASE, lucky])
    assert d["status"] == "none" and "not the planted signal" in d["reason"]
    d = T.decide([BASE, GBT, LOGREG, OVERTUNED])
    assert (d["status"], d["run_id"], d["run_name"]) == ("selected", "r-gbt", "gbt")


def test_the_selection_rule_is_written_down_in_the_decision_record():
    adr = (ROOT / "docs/quote_ai/decisions/adr-win-model-selection.md").read_text(encoding="utf-8")
    assert "0.05" in adr and "highest validation AUC" in adr and "nothing ships" in adr
    assert T.MAX_CALIBRATION_ERROR == 0.05 and T.MIN_TEST_AUC_GAIN == 0.05
    assert set(T.PLANTED_SIGNAL) <= set(F.FEATURES)
    assert all(word in catalog.SIGNAL for word in ("customer_current_price", "lead_time_days", "in_stock"))


def test_monotonic_constraints_only_name_model_features():
    assert set(T.MONOTONIC) <= set(F.FEATURES) and set(T.NUMERIC + T.BOOLEAN + T.CATEGORICAL) == set(F.FEATURES)


# ------------------------------------------------------------------ the simulation: is the signal there to find?

def test_on_the_synthetic_held_out_months_a_simple_model_beats_the_rule_and_finds_the_signal():
    """Win model suite: a logistic regression (standing in for the candidates the job trains) beats the rule baseline
    by at least 0.05 test AUC, is calibrated within 0.05, and its top drivers are the planted signal. On synthetic
    held-out months only: an educational simulation, not a claim about real sales."""
    facts = sales.quote_facts()
    gen = generate_dbxlab.generate()
    built = F.build(facts, sales.order_lines(facts), gen["customer_price_history"], gen["inventory_history"],
                    catalog.skus())
    train, valid, test = F.time_split(built["training"])
    yv, yt = [r["won"] for r in valid], [r["won"] for r in test]
    base = RuleBaseline().fit(train, [r["won"] for r in train])
    b = T.result("rule", T.evaluate(yv, base.predict_proba(valid), yt, base.predict_proba(test)),
                 {"price_vs_customer_current_pct": 1, "lead_time_days": 1})
    sample = train[::2]
    model = quick.QuickModel(list(F.FEATURES), epochs=200).fit(sample, [r["won"] for r in sample])
    lr = T.result("logreg", T.evaluate(yv, model.predict_proba(valid), yt, model.predict_proba(test)),
                  model.importance(), run_id="local")
    assert lr.test_auc >= b.test_auc + T.MIN_TEST_AUC_GAIN and lr.test_calibration_error < T.MAX_CALIBRATION_ERROR
    assert T.decide([b, lr])["status"] == "selected"
