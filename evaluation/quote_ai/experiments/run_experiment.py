"""AI-23: one variable per run. Each run changes exactly one thing against the baseline, runs the evaluation package
(the same version, VERSION), and logs every suite score with the cost per quote and the p95 latency. The rule, written
before any run (decision record 0005):

  a variant ships only if every required suite still meets its threshold (the AI-22 gate) AND the cost per quote or
  the p95 latency improves. Cheaper is only better if the gate still passes.

  --variable              --value                              what changes
  (none)                  -                                    the baseline
  comparison_prompt       v1 | v4                              E1: the compare_explain version (v4: shorter, table first)
  retriever               a quote_ai.search.retriever variant  E2: hybrid weight and reranking (hybrid_weighted_rerank)
  comparison_model        a model service (quote_ai_chat_small) E3/E3b: the comparison's model (needs the workspace)
  cache                   on | off                             E4: the model-call cache on extraction (second run cost)
  tuning                  -                                    E5: only if E1-E4 leave a required suite below threshold

  python evaluation/quote_ai/experiments/run_experiment.py --name baseline
  python evaluation/quote_ai/experiments/run_experiment.py --name E1 --variable comparison_prompt --value v4
  python evaluation/quote_ai/experiments/run_experiment.py --name E3 --variable comparison_model \
      --value quote_ai_chat_small --target dev --mlflow --experiment /Shared/quote_ai/experiments

Locally (no workspace) the comparison runs on the extractive stand-in: quality is the deterministic part, and the cost
is an estimate from the prompt and answer sizes at the prices in config/quote_ai/models.yml. With --target and a
model, the generator is the model service and the latency is measured. Experiments run in dev only.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent
sys.path.insert(0, str(EVAL))
sys.path.insert(0, str(EVAL.parents[1] / "src"))
import gate  # noqa: E402
import run_eval  # noqa: E402
import season4  # noqa: E402

VARIABLES = ("comparison_prompt", "retriever", "comparison_model", "cache", "tuning")
SUITES = "ci"                                  # the pure-code suites; the eval job adds the judge (--suites all)
TOKENS_PER_CHAR = 0.25                         # an estimate for the local run; the gateway's usage counts in dev


def tokens(text: str) -> int:
    return int(len(text) * TOKENS_PER_CHAR)


QUALITY_TARGET = {"retriever": "search.recall_at_5_heldout"}     # a quality lever names the score it is for
LATENCY_TOLERANCE = 1.10                                            # "no slower": within 10% (timing noise)


def ship(baseline: dict, candidate: dict, target_metric: str | None = None) -> tuple[bool, str]:
    """The rule written first. Results: {"gate_passed", "cost_per_quote", "p95_latency_s", "scores"}. A quality lever
    (E2: retrieval) ships when its target score improves at no extra cost and no slower; the others when cost or
    latency improves."""
    if not candidate.get("quality_measured", True):
        return False, "quality not measured (the stand-in wrote the answers): run it with --target dev and the judge"
    if not candidate["gate_passed"]:
        return False, "a required suite is below its threshold: not promoted"
    cheaper = candidate["cost_per_quote"] < baseline["cost_per_quote"]
    faster = candidate["p95_latency_s"] < baseline["p95_latency_s"]
    if target_metric and not (cheaper or faster):
        before, after = baseline["scores"].get(target_metric), candidate["scores"].get(target_metric)
        if before is not None and after is not None and after > before and                 candidate["cost_per_quote"] <= baseline["cost_per_quote"] and                 candidate["p95_latency_s"] <= baseline["p95_latency_s"] * LATENCY_TOLERANCE:
            return True, f"every suite meets its threshold and {target_metric} improves ({before} -> {after}) "                          f"at no extra cost"
    if not (cheaper or faster):
        return False, "the gate passes but neither cost nor latency improves: nothing to gain"
    return True, "every suite meets its threshold and " + " and ".join(
        w for w, ok in (("cost per quote", cheaper), ("p95 latency", faster)) if ok) + " improves"


def needs_tuning(results: list[dict]) -> tuple[bool, str]:
    """E5: tuning only if the cheaper levers (E1-E4) leave a required suite below its threshold."""
    gaps = sorted({s for r in results for s, ok in r.get("suites", {}).items() if not ok})
    if gaps:
        return True, f"a gap remains after E1-E4 in {gaps}: consider tuning, with its own decision record"
    return False, "E1-E4 leave no gap above threshold: no fine-tuning now"


def _args(variable: str | None, value: str | None, target: str | None):
    a = run_eval.parser().parse_args(["--suites", SUITES] + (["--target", target] if target else []))
    a.variant = "hybrid_section"
    if variable == "retriever":
        a.variant = value
    return a


def _comparison_cost(variable: str | None, value: str | None, generate) -> tuple[float, float, list[float]]:
    """(cost per comparison answer, mean answer tokens, latencies) over the comparison suite's questions."""
    from quote_ai.llm import prompts, rag
    from quote_ai.llm.client import cost_usd, model_for
    template = prompts.PROMPTS["compare_explain"][value][0] if variable == "comparison_prompt" else \
        prompts.COMPARE_EXPLAIN_V1
    service = model_for("comparison", overrides={"comparison": value} if variable == "comparison_model"
                        else None).service
    table = season4.lakeside_table()
    costs, outs, secs = [], [], []
    for c in season4.load("comparison"):
        start = time.perf_counter()
        out = season4.predict_comparison(c, "hybrid_section", generate)
        secs.append(time.perf_counter() - start)
        chunks = [{"chunk_id": r["chunk_id"], "truth_layer": "", "text": out["source_texts"].get(r["chunk_id"], "")}
                  for r in out["retrieved"]]
        prompt = prompts.fill(template, question=c["question"], table=rag._table_md(table),
                              context=rag.context_text(chunks))
        answer = json.dumps({"statements": out["statements"]})
        outs.append(tokens(answer))
        costs.append(cost_usd(service, tokens(prompt), tokens(answer)))
    return statistics.mean(costs), statistics.mean(outs), secs


def run(name: str, variable: str | None = None, value: str | None = None, target: str | None = None) -> dict:
    """One experiment run: the package with one override, its scores, cost per quote and p95 latency."""
    if variable and variable not in VARIABLES:
        raise SystemExit(f"--variable must be one of {VARIABLES}")
    if variable == "tuning":
        raise SystemExit("E5 is a decision, not a run: see needs_tuning() and decision record 0005")
    a = _args(variable, value, target)
    generate = None
    season4.COMPARE_TEMPLATE = None
    if variable == "comparison_prompt":
        from quote_ai.llm import prompts
        season4.COMPARE_TEMPLATE = prompts.PROMPTS["compare_explain"][value][0]
    if target and variable in ("comparison_model", "comparison_prompt"):
        from quote_ai.llm import rag
        from quote_ai.llm.client import model_for
        m = model_for("comparison", overrides={"comparison": value} if variable == "comparison_model" else None)
        generate = rag.llm_generate(m.endpoint, max_tokens=m.max_tokens)
        a.variant, a.endpoint = "build", m.endpoint
    results, passed = run_eval.run_gate(a)
    scores = {f"{r['suite']}.{c['metric']}": c["value"] for r in results for c in r["checks"]
              if isinstance(c["value"], (int, float))}
    if variable in ("retriever", "comparison_prompt", "comparison_model", "cache", None):
        held = [c for c in season4.load("search") if c.get("split") == "heldout"]
        import scorers
        scores["search.recall_at_5_heldout"] = round(statistics.mean(
            scorers.recall_at_5(season4.predict_search(c, a.variant), season4.expectations("search", c))
            for c in held), 4)
    compare_cost, answer_tokens, secs = _comparison_cost(variable, value, generate)
    extraction_cost = second_run_extraction_cost(variable == "cache" and value == "on")   # baseline: no cache
    out = {"name": name, "variable": variable, "value": value, "package": gate.package_version(),
           "gate_passed": passed, "quality_measured": not (variable == "comparison_model" and generate is None),
           "suites": {r["suite"]: r["passed"] for r in results if r["required"]},
           "scores": scores, "comparison_answer_tokens": answer_tokens,
           "cost_per_quote": round(compare_cost + extraction_cost, 6),
           "p95_latency_s": round(sorted(secs)[int(0.95 * (len(secs) - 1))], 4)}
    season4.COMPARE_TEMPLATE = None
    return out


def second_run_extraction_cost(cache_on: bool) -> float:
    """E4: the product facts extraction run twice over the same 100 GUDID descriptions (unchanged records); the
    cost of the second run per quote-relevant record. With the cache, every record is a hit."""
    from quote_ai.llm.cache import LlmCache, version_of
    from quote_ai.llm.client import cost_usd, model_for
    m = model_for("extraction")
    cache, calls = LlmCache(), []
    cases = run_eval.load("product_facts")
    for _ in range(2):
        calls.clear()
        for c in cases:
            payload = {"description": c["description"]}
            call = lambda: calls.append(1) or {"ok": True}  # noqa: E731  the model call, counted
            if cache_on:
                cache.get_or_call("glove_attributes", payload, version_of("v1", m.service, 1), call)
            else:
                call()
    per_call = cost_usd(m.service, 400, 80)
    return round(len(calls) * per_call / max(len(cases), 1), 6)


def log_mlflow(result: dict, experiment: str) -> str:
    """One MLflow run per experiment, the variable as a param. Verify before recording: the experiment path in dev."""
    import mlflow
    mlflow.set_experiment(experiment)
    with mlflow.start_run(run_name=result["name"]) as r:
        mlflow.log_params({"variable": result["variable"] or "baseline", "value": result["value"] or "-",
                           "package": result["package"]})
        mlflow.log_metrics({k: float(v) for k, v in result["scores"].items()}
                           | {"cost_per_quote": result["cost_per_quote"], "p95_latency_s": result["p95_latency_s"],
                              "gate_passed": float(result["gate_passed"])})
        return r.info.run_id


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--name", required=True)
    p.add_argument("--variable", choices=VARIABLES + ("",))
    p.add_argument("--value")
    p.add_argument("--target", help="dev: use the model services (experiments run in dev only)")
    p.add_argument("--mlflow", action="store_true")
    p.add_argument("--experiment", default="/Shared/quote_ai/experiments")
    p.add_argument("--compare-to", help="a baseline result JSON: print the ship decision")
    a = p.parse_args(argv)
    a.variable, a.value = a.variable or None, a.value or None      # the job passes "" for the baseline
    if a.target and a.target != "dev":
        p.error("experiments run in dev only")
    result = run(a.name, a.variable, a.value, a.target)
    if a.mlflow:
        result["run_id"] = log_mlflow(result, a.experiment)
    if a.compare_to:
        ok, why = ship(json.loads(Path(a.compare_to).read_text(encoding="utf-8")), result,
                       QUALITY_TARGET.get(a.variable or ""))
        result["ship"] = {"decision": ok, "why": why}
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
