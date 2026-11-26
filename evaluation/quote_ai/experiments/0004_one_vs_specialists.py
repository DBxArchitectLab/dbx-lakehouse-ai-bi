"""AI-21's experiment: one agent (design A, the end of AI-20) against four specialists routed by rule (design B), on the
same 100 end-to-end requests (evaluation/quote_ai/v1/workflow, kind e2e), with the same model, tools and gateway.
The decision rule is written before the run:

  adopt B only if it gains >= 5 points of quality (the mean of task success, tool-choice accuracy and groundedness,
  in points) at <= 20% extra tokens and <= 20% extra p95 latency per request; otherwise keep A.

The result goes into docs/quote_ai/decisions/0004-orchestration-and-approval.md (between the RESULTS markers),
whichever design wins.

  python evaluation/quote_ai/experiments/0004_one_vs_specialists.py                    # the stand-in model (local)
  python evaluation/quote_ai/experiments/0004_one_vs_specialists.py --endpoint quote_ai_chat_dev --write
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import season5  # noqa: E402

RULE = {"min_quality_gain": 5.0, "max_cost_increase": 0.20, "max_latency_increase": 0.20}
RECORD = HERE.parents[2] / "docs" / "quote_ai" / "decisions" / "0004-orchestration-and-approval.md"
BEGIN, END = "<!-- RESULTS:BEGIN -->", "<!-- RESULTS:END -->"


@dataclass
class Result:
    design: str
    quality: float          # points, 0-100
    task_success: float
    tool_choice: float
    groundedness: float
    tokens: float           # per request
    p95_latency: float      # seconds
    gates: dict


def run_package(design: str, client=None, endpoint: str = "standin") -> Result:
    r = season5.run("workflow", design=design, client=client, endpoint=endpoint, kinds=("e2e",))
    m = r["metrics"]
    quality = round(100 * (m["task_success"] + m["tool_choice_accuracy"] + m["groundedness"]) / 3, 1)
    return Result(design, quality, m["task_success"], m["tool_choice_accuracy"], m["groundedness"],
                  m["tokens_per_request"], m["p95_seconds"], r["gates"])


def decide(a: Result, b: Result, rule: dict = RULE) -> str:
    """The rule, as written before the run. A gate above zero disqualifies a design outright."""
    if any(b.gates.values()):
        return "one_agent"
    if any(a.gates.values()):
        return "specialists"
    gains = b.quality - a.quality >= rule["min_quality_gain"]
    cost_ok = b.tokens <= a.tokens * (1 + rule["max_cost_increase"])
    latency_ok = b.p95_latency <= a.p95_latency * (1 + rule["max_latency_increase"])
    return "specialists" if gains and cost_ok and latency_ok else "one_agent"


def table(a: Result, b: Result, keep: str, model: str) -> str:
    rows = [("Quality (points)", a.quality, b.quality), ("Task success", a.task_success, b.task_success),
            ("Tool-choice accuracy", a.tool_choice, b.tool_choice), ("Groundedness", a.groundedness, b.groundedness),
            ("Tokens per request", a.tokens, b.tokens), ("p95 latency (s)", a.p95_latency, b.p95_latency)]
    out = [f"Run {date.today().isoformat()}, model: {model}, 100 requests (workflow suite, kind e2e).", "",
           "| Measure | A: one agent | B: specialists |", "| --- | --- | --- |"]
    out += [f"| {n} | {x} | {y} |" for n, x, y in rows]
    out += ["", f"Gates (must be 0): A {json.dumps(a.gates)}; B {json.dumps(b.gates)}", "",
            f"**Decision by the rule: keep {'B (specialists)' if keep == 'specialists' else 'A (one agent)'}.**"]
    return "\n".join(out)


def write_decision_record(a: Result, b: Result, keep: str, model: str, path: Path = RECORD) -> None:
    text = path.read_text(encoding="utf-8")
    head, rest = text.split(BEGIN, 1)
    _, tail = rest.split(END, 1)
    path.write_text(f"{head}{BEGIN}\n{table(a, b, keep, model)}\n{END}{tail}", encoding="utf-8")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--endpoint", help="the chat model service (default: the local stand-in model)")
    p.add_argument("--write", action="store_true", help="write the numbers into decision record 0004")
    a_ = p.parse_args(argv)
    client = None
    if a_.endpoint:
        from quote_ai.llm.client import workspace_client
        client = workspace_client()
    model = a_.endpoint or "stand-in (deterministic, local)"
    a = run_package("one_agent", client, a_.endpoint or "standin")
    b = run_package("specialists", client, a_.endpoint or "standin")
    keep = decide(a, b)
    print(table(a, b, keep, model))
    if a_.write:
        write_decision_record(a, b, keep, model)
    return 0


if __name__ == "__main__":
    sys.exit(main())
