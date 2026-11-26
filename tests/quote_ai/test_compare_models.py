"""Model comparison (AI-04): the scoring, parsing and cost logic, with no model call."""
from types import SimpleNamespace

import pytest

from quote_ai.llm import compare_models as cm
from quote_ai.llm.client import Reply, Result


def test_score_counts_matching_fields():
    assert cm.score({"material": "nitrile"}, {"material": "nitrile"}) == 0.2


def test_not_stated_is_a_valid_answer_and_a_guess_is_a_hallucination():
    truth = {"material": "nitrile", "powder_free": True, "sterile": None, "length_in": 9.5, "texture": None}
    good = {"material": "Nitrile", "powder_free": True, "sterile": None, "length_in": "9.5", "texture": None}
    assert cm.score(good | {"length_in": 9.5}, truth) == 1.0
    guess = good | {"length_in": 9.5, "sterile": False}
    assert cm.score(guess, truth) == 0.8 and cm.hallucinations(guess, truth) == ["sterile"]


def test_parse_json_tolerates_a_fence_and_refuses_prose():
    assert cm.parse_json('```json\n{"material": "nitrile"}\n```') == {"material": "nitrile"}
    assert cm.parse_json("It is a nitrile glove.") == {} and cm.parse_json(None) == {}


def test_the_suite_has_100_labelled_gloves_with_every_field():
    cases = cm.load_cases()
    assert len(cases) == 100                                       # 30 from AI-04, 70 more in AI-08
    assert all(set(cm.FIELDS) <= set(c["expected"]) for c in cases)


def test_evaluate_sums_tokens_and_latency_per_endpoint():
    def ask(prompt, endpoint, system):
        assert "null" in system.lower()
        return Reply('{"material": "nitrile"}', SimpleNamespace(prompt_tokens=40, completion_tokens=10), 0.5)
    cases = cm.load_cases()[:2]
    m = cm.evaluate("ep", cases, ask)["metrics"]
    assert m["tokens_in"] == 80 and m["tokens_out"] == 20 and m["latency_avg_s"] == 0.5 and m["json_parsed"] == 1


def test_cost_per_1000_gloves():
    # 30 cases, 3,000 tokens in and 600 out, at $0.50 / $1.50 per million: $0.08 per 1,000 gloves
    assert cm.cost_per_1000(3000, 600, 30, 0.5, 1.5) == pytest.approx(0.08)


def test_every_reply_carries_tokens_and_latency():
    r = Result("x", SimpleNamespace(prompt_tokens=7, completion_tokens=3, total_tokens=10), 1.25)
    assert (r.prompt_tokens, r.completion_tokens, r.latency_s) == (7, 3, 1.25)
