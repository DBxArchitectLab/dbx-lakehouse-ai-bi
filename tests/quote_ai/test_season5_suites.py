"""The Season 5 suites as files and as runs (AI-18 to AI-21): pricing and reliability at zero failures, the workflow
suite's scorers on toy data, the overlapping tool turning tool choice down, and the experiment's decision rule."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation" / "quote_ai"))
import scorers  # noqa: E402
import season5  # noqa: E402


def experiment():
    spec = importlib.util.spec_from_file_location(
        "exp0004", ROOT / "evaluation/quote_ai/experiments/0004_one_vs_specialists.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["exp0004"] = mod                         # dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


def test_the_suite_files():
    pricing, reliability, workflow = (season5.load(s) for s in ("pricing", "reliability", "workflow"))
    assert {c["expected"]["approval"] for c in pricing if c["kind"] == "band"} == {"NONE", "MANAGER", "REFUSED"}
    assert {c["episode"] for c in reliability} == {"AI-18", "AI-19", "AI-20", "AI-21"}
    assert sum(c["kind"] == "e2e" for c in workflow) == 100
    assert len({c["case_id"] for c in workflow}) == len(workflow)


@pytest.mark.parametrize("suite", ["pricing", "reliability"])
def test_zero_tolerance_suites_pass(suite):
    r = season5.run(suite)
    assert r["passed"] and not any(r["gates"].values()), r["failing_cases"]


def test_the_workflow_suite_on_the_stand_in():
    r = season5.run("workflow", kinds=("tool_choice", "boundary", "resume", "fallback"))
    assert r["passed"] and r["metrics"]["tool_choice_accuracy"] == 1.0 and r["metrics"]["task_success"] == 1.0


def test_the_overlapping_tool_lowers_tool_choice_accuracy():
    r = season5.run("workflow", variant="overlapping", kinds=("tool_choice",))
    assert r["metrics"]["tool_choice_accuracy"] < 0.9 and r["passed"]


def test_workflow_scorers_on_toy_data():
    case = {"expect_first_tool": "get_evidence", "forbid_tools": ["search_candidates"],
            "expect_in_answer": ["needs evidence"], "expect_proposal": "ISSUED"}
    good = {"tools": ["get_evidence"], "results": [{"name": "get_evidence", "source": "lakehouse_copy"}],
            "proposals": ["ISSUED"], "answer": "Glove B needs evidence.", "violations": [], "state_changed": False}
    bad = good | {"tools": ["search_candidates", "get_evidence"], "results": [{"name": "x", "source": None}],
                  "violations": ["price $7.95 is not a number any tool returned"], "state_changed": True,
                  "proposals": []}
    assert [f(good, case) for f in scorers.SEASON5_SCORERS] == [1.0, 1.0, 1.0, 0, 0, 0]
    assert [f(bad, case) for f in scorers.SEASON5_SCORERS] == [0.0, 0.0, 0.0, 1, 1, 1]


def test_the_decision_rule_written_before_the_run():
    exp = experiment()
    gates = {"state_changes": 0}
    a = exp.Result("one_agent", 80.0, .8, .8, .8, 1000, 2.0, gates)
    assert exp.decide(a, exp.Result("specialists", 86.0, .86, .86, .86, 1150, 2.2, gates)) == "specialists"
    assert exp.decide(a, exp.Result("specialists", 84.0, .84, .84, .84, 900, 1.0, gates)) == "one_agent"  # < 5
    assert exp.decide(a, exp.Result("specialists", 90.0, .9, .9, .9, 1300, 2.0, gates)) == "one_agent"   # +30%
    assert exp.decide(a, exp.Result("specialists", 90.0, .9, .9, .9, 1000, 2.5, gates)) == "one_agent"   # latency
    assert exp.decide(a, exp.Result("specialists", 99.0, .99, .99, .99, 900, 1.0, {"state_changes": 1})) \
        == "one_agent"                                                                                     # a gate


def test_decision_record_0004_holds_the_numbers():
    text = (ROOT / "docs/quote_ai/decisions/0004-orchestration-and-approval.md").read_text(encoding="utf-8")
    block = text.split("<!-- RESULTS:BEGIN -->")[1].split("<!-- RESULTS:END -->")[0]
    assert "| Quality (points) |" in block and "Decision by the rule" in block
