"""The evaluation gate (AI-22): one versioned package, one release rule (v1/thresholds.yml), one runner that exits 1
below a threshold. The rule itself is tested, then the runner on the pure-code suites, then the regressions it must
block: a weaker extraction prompt (elig-007) and a relaxed latex rule."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "evaluation" / "quote_ai"
sys.path.insert(0, str(EVAL))
import gate  # noqa: E402
import run_eval  # noqa: E402
import season4  # noqa: E402

SIXTEEN = {"ingestion", "mapping", "requirements", "model_access", "product_facts", "genie", "win_model",
           "eligibility", "fulfilment", "search", "comparison", "pricing", "workflow", "reliability",
           "data_authorization", "operations"}


# ------------------------------------------------------------------ the package and its rule

def test_the_package_is_versioned_and_declares_sixteen_suites():
    assert gate.package_version().count(".") == 2
    suites = gate.load_thresholds()
    assert set(suites) == SIXTEEN
    assert all(s["required"] for s in suites.values())       # AI-24: data authorization; AI-26: operations
    assert suites["data_authorization"]["thresholds"]["unauthorized"] == {"max": 0}
    assert suites["operations"]["runs"] == "ci" and suites["operations"]["thresholds"]["stale_issued"] == {"max": 0}
    for name in SIXTEEN:
        assert (EVAL / "v1" / name).is_dir(), name


def test_every_runnable_suite_folder_has_cases():
    for name, s in gate.load_thresholds().items():
        if s["runs"] in ("ci", "workspace") and name != "ingestion":
            assert (EVAL / "v1" / name / "cases.jsonl").read_text(encoding="utf-8").strip(), name
    assert len(run_eval.load("requirements")) == 100
    assert len(run_eval.load("search")) >= 50


def test_the_rule_names_the_zero_tolerance_gates():
    s = gate.load_thresholds()
    assert s["mapping"]["thresholds"]["unverified_offered"] == {"max": 0}
    assert s["eligibility"]["thresholds"]["unknown_as_match"] == {"max": 0}
    assert s["requirements"]["thresholds"]["field_accuracy"] == {"min": 0.95}
    assert s["search"]["thresholds"]["recall_at_5"] == {"min": 0.85}
    assert s["comparison"]["thresholds"]["grounded_rate"]["needs"] == "judge"
    assert s["workflow"]["thresholds"]["commits_outside_quote_service"] == {"max": 0}


def test_a_bad_thresholds_file_is_refused(tmp_path):
    f = tmp_path / "t.yml"
    f.write_text(yaml.safe_dump({"suites": {"x": {"episode": "AI-00", "required": True, "runs": "nightly",
                                                  "thresholds": {}}}}))
    with pytest.raises(ValueError, match="runs"):
        gate.load_thresholds(f)
    f.write_text(yaml.safe_dump({"suites": {"x": {"episode": "AI-00", "required": True, "runs": "ci",
                                                  "thresholds": {"m": {"below": 1}}}}}))
    with pytest.raises(ValueError, match="max, min or equals"):
        gate.load_thresholds(f)


SPEC = {"required": True, "thresholds": {"violations": {"max": 0}, "recall": {"min": 0.85},
                                         "grounded": {"min": 0.9, "needs": "judge"}}}


def test_check_pass_fail_and_not_run():
    r = gate.check("s", SPEC, {"violations": 0, "recall": 0.9})
    assert r["passed"] and [c["status"] for c in r["checks"]] == ["pass", "pass", "not_run"]
    r = gate.check("s", SPEC, {"violations": 1, "recall": 0.9})
    assert not r["passed"] and r["checks"][0]["status"] == "fail"
    r = gate.check("s", SPEC, {"violations": 0, "recall": 0.84})
    assert not r["passed"]


def test_a_metric_missing_although_its_input_exists_fails():
    r = gate.check("s", SPEC, {"violations": 0, "recall": 0.9}, frozenset({"judge"}))
    assert not r["passed"] and r["checks"][2] == {"metric": "grounded", "value": None, "threshold": {"min": 0.9},
                                                  "status": "fail", "reason": "not produced"}
    assert gate.check("s", SPEC, {}, frozenset())["passed"] is False         # a crashed suite never passes


def test_only_required_suites_decide_the_release():
    failing_declared = {"suite": "data_authorization", "required": False, "passed": False, "checks": []}
    ok = {"suite": "pricing", "required": True, "passed": True, "checks": []}
    assert gate.verdict([ok, failing_declared])
    assert not gate.verdict([ok | {"passed": False}])


def test_select_scopes():
    s = gate.load_thresholds()
    ci = gate.select(s, "ci")
    assert {"eligibility", "pricing", "workflow", "reliability", "ingestion"} <= set(ci)
    assert "requirements" not in ci and "genie" not in ci and "operations" in ci     # AI-26: operations runs in CI
    assert set(gate.select(s, "all")) >= set(ci) | {"requirements", "genie", "product_facts"}
    assert gate.select(s, "pricing,search") == ["pricing", "search"]
    with pytest.raises(ValueError):
        gate.select(s, "pricing,nope")


def test_result_rows_carry_sha_and_package_version():
    r = gate.check("comparison", SPEC, {"violations": 0, "recall": 0.9})
    rows = gate.result_rows([r], run_id="r1", sha="abc", version="1.0.0",
                            costs={"comparison": {"model_calls": 30, "cost_usd": 0.4}})
    assert len(rows) == 3 and {x["git_sha"] for x in rows} == {"abc"}
    assert rows[0] | {"run_at": None} == {"run_id": "r1", "suite": "comparison", "metric": "violations", "value": 0.0,
                                          "threshold": "{'max': 0}", "status": "pass", "required": True,
                                          "git_sha": "abc", "package_version": "1.0.0", "model_calls": 30,
                                          "cost_usd": 0.4, "run_at": None}
    assert rows[2]["value"] is None and rows[2]["status"] == "not_run"


def test_the_results_table_matches_the_rows():
    sql = (EVAL / "eval_tables.sql").read_text(encoding="utf-8")
    block = sql[sql.index("{eval}.eval_results ("):sql.index(") COMMENT 'Every evaluation run")]
    cols = [l.split()[0] for l in block.splitlines()[1:] if l.strip()]
    row = gate.result_rows([gate.check("s", SPEC, {"violations": 0, "recall": 0.9})], sha="x", version="1")[0]
    assert cols == list(row)


# ------------------------------------------------------------------ the runner

def test_the_ci_gate_passes_on_this_branch(capsys):
    assert run_eval.main(["--suites", "ci"]) == 0
    out = capsys.readouterr().out
    assert "GATE PASSED" in out and "not_run" in out          # the judge is not in CI: reported, not passed


def test_the_runner_needs_no_pytest_on_the_path():
    """season5 imported tests/quote_ai helpers until AI-22; the package now runs on quote_ai.fixtures alone."""
    code = (EVAL / "season5.py").read_text(encoding="utf-8")
    assert "tests" not in code.split('"""', 2)[2] and "quote_ai.fixtures" in code
    r = subprocess.run([sys.executable, str(EVAL / "run_eval.py"), "--suite", "reliability"],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr


def test_ingestion_and_win_model_suites():
    r = run_eval.run_ingestion()
    assert r["bad_config_accepted"] == 0 and r["replay_duplicates"] == 0
    assert run_eval.run_win_model()["decision_failures"] == 0


def test_model_access_verdicts_from_the_notebooks_outcomes(tmp_path):
    good = {"allowed": ["allowed"], "no-grant": ["no_grant"], "over-the-limit": ["allowed"] * 20 + ["rate_limited"] * 5,
            "blocked-content": ["blocked"], "sec-06": ["blocked"], "sec-07": ["blocked"]}
    f = tmp_path / "o.json"
    f.write_text(json.dumps(good))
    args = run_eval.parser().parse_args(["--suite", "model_access", "--outcomes", str(f)])
    assert run_eval.run_model_access(args) == {"unauthorized": 0, "unexpected_outcomes": 0, "failing": []}
    f.write_text(json.dumps(good | {"no-grant": ["allowed"]}))
    r = run_eval.run_model_access(args)
    assert r["unauthorized"] == 1 and r["failing"] == ["no-grant"]


# ------------------------------------------------------------------ the regressions the gate blocks

def test_elig_007_blocks_an_extraction_that_misses_the_footnote():
    case = next(c for c in season4.load("eligibility") if c["case_id"] == "elig-007")
    exp = season4.expectations("eligibility", case)
    ok = season4.score_case("eligibility", season4.predict_eligibility(case), exp)
    assert ok == {"eligibility_case_failures": 0, "eligibility_violations": 0, "unknown_as_match": 0}

    from quote_ai.llm.requirements_schema import Requirements as Extracted
    missed = Extracted.model_validate({"material": "nitrile", "use": "exam", "quantity": 400, "unit": "box",
                                       "powder_free": {"value": True, "hard": True},
                                       "chemo_tested": {"value": True, "hard": True},
                                       "sizes": ["S", "M", "L", "XL"], "delivery_days": 10})   # no latex_free
    out = season4.predict_eligibility(case, extract=lambda text: missed)
    assert {"GLOVE-E-M"} & {o["sku_id"] for o in out["shortlist"]}            # Glove E reached the shortlist
    assert season4.score_case("eligibility", out, exp)["eligibility_case_failures"] == 1


def test_the_footnote_rule_is_in_the_production_prompt_only_from_v4():
    from quote_ai.llm import prompts
    assert prompts.PRODUCTION == "v4" and prompts.FOOTNOTE_RULE.strip() in prompts.VERSIONS["v4"][0]
    assert "footnote" not in prompts.VERSIONS["v3"][0]
    footnotes = [c for c in run_eval.load("requirements") if c["id"].startswith(("footnote-", "ps-"))]
    assert len(footnotes) >= 10 and all("\n" in c["request"] for c in footnotes)


def test_a_relaxed_latex_rule_fails_the_gate():
    r = season4.run("eligibility", "relax_latex")
    res = gate.check("eligibility", gate.load_thresholds()["eligibility"],
                     {k: r["gates"][k] for k in ("eligibility_violations", "unknown_as_match",
                                                 "eligibility_case_failures")})
    assert not res["passed"] and not gate.verdict([res])
