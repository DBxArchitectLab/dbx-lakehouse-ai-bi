"""AI-23's experiment runner: one variable per run, and the rule written before any run (decision record 0005)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation" / "quote_ai" / "experiments"))
import run_experiment as X  # noqa: E402

BASE = {"gate_passed": True, "cost_per_quote": 0.0090, "p95_latency_s": 14.0, "scores": {"x": 0.86}}


def test_e3_a_smaller_model_that_passes_the_gate_and_costs_less_ships():
    e3 = {"gate_passed": True, "cost_per_quote": 0.0023, "p95_latency_s": 9.0, "scores": {}}
    assert X.ship(BASE, e3)[0]


def test_e3b_the_smallest_model_is_cheaper_but_fails_the_gate_and_does_not_ship():
    e3b = {"gate_passed": False, "cost_per_quote": 0.0011, "p95_latency_s": 6.0, "scores": {}}
    ok, why = X.ship(BASE, e3b)
    assert not ok and "below its threshold" in why


def test_no_gain_no_ship_and_a_quality_lever_needs_its_score_to_improve():
    same = dict(BASE)
    assert not X.ship(BASE, same)[0]
    better = BASE | {"scores": {"x": 0.91}}
    assert X.ship(BASE, better, "x")[0] and not X.ship(BASE, better | {"cost_per_quote": 0.01}, "x")[0]


def test_a_model_change_measured_on_the_stand_in_never_ships():
    local = {"gate_passed": True, "cost_per_quote": 0.001, "p95_latency_s": 1.0, "quality_measured": False}
    assert not X.ship(BASE, local)[0]


def test_e5_tuning_only_when_a_gap_remains():
    assert X.needs_tuning([{"suites": {"comparison": True}}])[0] is False
    need, why = X.needs_tuning([{"suites": {"comparison": False, "search": True}}])
    assert need and "comparison" in why


def test_one_variable_per_run_and_dev_only():
    with pytest.raises(SystemExit):
        X.run("bad", "temperature", "0.2")
    with pytest.raises(SystemExit):
        X.run("E5", "tuning")
    with pytest.raises(SystemExit):
        X.main(["--name", "x", "--target", "prod"])


def test_e4_the_cache_makes_the_second_run_free():
    assert X.second_run_extraction_cost(True) == 0.0 < X.second_run_extraction_cost(False)


def test_e1_shorter_prompt_lowers_the_comparison_cost_at_the_same_scores():
    base = X.run("baseline")
    e1 = X.run("E1", "comparison_prompt", "v4")
    assert e1["gate_passed"] and e1["cost_per_quote"] < base["cost_per_quote"]
    assert e1["scores"]["comparison.layer_cited"] == base["scores"]["comparison.layer_cited"] == 1.0
