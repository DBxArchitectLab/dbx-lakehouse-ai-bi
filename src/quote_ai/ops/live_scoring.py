"""Live scoring (AI-26): the same scorers as the evaluation gate (AI-22), on a daily sample of production traces.
The test set says how QuoteAI did on our cases; this says how it is doing on real requests this week. A confident
wrong answer isn't an error, so errors and latency are not enough.

  sample(traces, n, seed)    a deterministic sample (slo.yml: 50 a day, seed 1)
  score(sample, judge=None)  the gate's scorers (evaluation/quote_ai/scorers.py, imported, never copied):
                             shortlist traces -> eligibility_violations, unknown_as_match, unknown_share;
                             comparison traces -> layer_cited, and grounded_rate from the grounded_comparison judge
                             when a judge is given (the workspace: mlflow_judge)
  usage_rows(traces)         {monitoring}.trace_usage rows: quote, step, service, tokens, latency, status
  run(traces, day, ...)      both, for the quote_ai_live_scoring job

A trace here is a plain dict: {trace_id, day, quote_id, kind (shortlist | comparison | ...), outputs, spans:
[{step, service, prompt_tokens, completion_tokens, latency_ms, status}]}. search_traces() turns MLflow traces into it
in the workspace; the tests and the Operations suite build them from the fixtures. Sampled traces are read as the
job's identity and only scores and counts are stored: no request text, no customer data, no rep (AI-24's masks
apply to anything the traces quote).
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

ROOT = Path(__file__).resolve().parents[3]
EVAL = ROOT / "evaluation" / "quote_ai"


def gate_scorers():
    """evaluation/quote_ai/scorers.py: the scorers the release rule uses. Imported from the evaluation package (synced
    with the bundle), so production is scored by exactly the code that gates a release."""
    if str(EVAL) not in sys.path:
        sys.path.insert(0, str(EVAL))
    import scorers
    return scorers


def sample(traces: list[dict], n: int = 50, seed: int = 1) -> list[dict]:
    traces = sorted(traces, key=lambda t: t["trace_id"])
    return traces if len(traces) <= n else random.Random(seed).sample(traces, n)


def score(traces: Iterable[dict], judge: Callable[[list[dict]], float] | None = None) -> list[dict]:
    """One row per scorer: {scorer, value, n}. A scorer with no traces of its kind is left out (not a pass)."""
    sc = gate_scorers()
    traces = list(traces)
    shortlist = [t["outputs"] for t in traces if t.get("kind") == "shortlist"]
    comparison = [t["outputs"] for t in traces if t.get("kind") == "comparison"]
    out = []
    if shortlist:
        checks = [c for o in shortlist for r in o["results"] for c in r["checks"]]
        out += [{"scorer": "eligibility_violations", "value": float(sum(sc.eligibility_violations(o, {})
                                                                         for o in shortlist)), "n": len(shortlist)},
                {"scorer": "unknown_as_match", "value": float(sum(sc.unknown_as_match(o, {}) for o in shortlist)),
                 "n": len(shortlist)},
                {"scorer": "unknown_share", "value": round(sum(c["result"] == "unknown" for c in checks)
                                                           / max(1, len(checks)), 4), "n": len(shortlist)}]
    if comparison:
        out.append({"scorer": "layer_cited", "value": round(sum(sc.layer_cited(o, {}) for o in comparison)
                                                            / len(comparison), 4), "n": len(comparison)})
        if judge is not None:
            out.append({"scorer": "grounded_rate", "value": judge(comparison), "n": len(comparison)})
    return out


def usage_rows(traces: Iterable[dict]) -> list[dict]:
    return [{"day": t["day"], "quote_id": t["quote_id"], "trace_id": t["trace_id"], "step": s["step"],
             "service": s.get("service") or "none", "prompt_tokens": s.get("prompt_tokens", 0),
             "completion_tokens": s.get("completion_tokens", 0), "latency_ms": s.get("latency_ms", 0),
             "status": s.get("status", "ok")} for t in traces for s in t.get("spans", [])]


def run(traces: list[dict], day: str, n: int = 50, seed: int = 1,
        judge: Callable[[list[dict]], float] | None = None) -> tuple[list[dict], list[dict]]:
    """(live_scores rows for the day, trace_usage rows for every trace of the day)."""
    todays = [t for t in traces if str(t["day"])[:10] == day]
    at = datetime.now(timezone.utc)
    scores = [r | {"day": day, "scored_at": at} for r in score(sample(todays, n, seed), judge)]
    return scores, usage_rows(todays)


# ------------------------------------------------------------------ the workspace (the quote_ai_live_scoring job)

def mlflow_judge(experiment: str | None = None) -> Callable[[list[dict]], float]:
    """grounded_rate on sampled comparisons with the gate's guideline judge (scorers.grounded_judge, through Unity
    Gateway). Verify before recording: mlflow.genai.evaluate(data=[{"inputs", "outputs"}], scorers=[...]) and the
    metric name "grounded_comparison/mean" (as in run_eval.run_judge)."""
    def judge(outputs: list[dict]) -> float:
        import mlflow
        sc = gate_scorers()
        if experiment:
            mlflow.set_experiment(experiment)
        data = [{"inputs": {"question": o.get("question", "")}, "outputs": {"answer": o.get("answer", ""),
                                                                           "statements": o["statements"]}}
                for o in outputs]
        r = mlflow.genai.evaluate(data=data, scorers=[sc.grounded_judge()])
        return float(r.metrics.get("grounded_comparison/mean"))
    return judge


def search_traces(experiment: str, since: datetime, max_results: int = 2000) -> list[dict]:
    """The production traces of the app's experiment since `since`, as plain dicts. Verify before recording:
    mlflow.search_traces(experiment_names=, filter_string="timestamp_ms > ...", return_type="list"), the trace tags
    the app sets (quote_id, step: QuoteService._span and the routes), span attributes for token usage
    (mlflow.chat.tokenUsage) and how a StaleEvidence refusal shows on the issue span."""
    import mlflow
    found = mlflow.search_traces(experiment_names=[experiment], max_results=max_results, return_type="list",
                                 filter_string=f"timestamp_ms > {int(since.timestamp() * 1000)}")
    out = []
    for tr in found:
        tags = tr.info.tags or {}
        day = datetime.fromtimestamp(tr.info.request_time / 1000, tz=timezone.utc).date().isoformat()
        spans = []
        for s in tr.data.spans:
            usage = (s.attributes or {}).get("mlflow.chat.tokenUsage") or {}
            status = "stale_refused" if "StaleEvidence" in str(getattr(s, "status", "")) else (
                "error" if "ERROR" in str(getattr(s, "status", "")) else "ok")
            spans.append({"step": (s.attributes or {}).get("quote_ai.step", s.name), "service":
                          (s.attributes or {}).get("quote_ai.service"), "prompt_tokens": usage.get("input_tokens", 0),
                          "completion_tokens": usage.get("output_tokens", 0),
                          "latency_ms": int((s.end_time_ns - s.start_time_ns) / 1e6) if s.end_time_ns else 0,
                          "status": status})
        out.append({"trace_id": tr.info.trace_id, "day": day, "quote_id": tags.get("quote_id"),
                    "kind": tags.get("quote_ai.kind"), "outputs": _outputs(tr), "spans": spans})
    return out


def _outputs(tr) -> dict:
    import json
    try:
        return json.loads(tr.data.response) if isinstance(tr.data.response, str) else (tr.data.response or {})
    except ValueError:
        return {}
