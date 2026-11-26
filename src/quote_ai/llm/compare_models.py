"""Which model reads a glove description best, and at what cost? (AI-04)

Every endpoint gets the same prompt and the same hand-labelled gloves (evaluation/quote_ai/v1/product_facts). Each
answer is parsed as JSON and scored field by field against the label, where "not stated" (null) is a valid answer
and a value the text does not support is a hallucination. One MLflow run per endpoint: accuracy, tokens and latency,
side by side in one experiment.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from quote_ai.llm.client import Reply, chat

FIELDS = ["material", "powder_free", "sterile", "length_in", "texture"]
SYSTEM = ("Extract glove attributes as JSON with keys " + ", ".join(FIELDS) + ". Use null when not stated. "
          "material is one of nitrile, latex, vinyl, polyisoprene; length_in is a number in inches; texture is one "
          "of fingertip, fully, smooth. Answer with the JSON object only.")
CASES = Path(__file__).resolve().parents[3] / "evaluation" / "quote_ai" / "v1" / "product_facts" / "cases.jsonl"


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip().lower()
        return None if v in ("", "null", "none", "not stated") else v
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return round(float(v), 2)
    return v


def score(pred: dict, truth: dict) -> float:
    """The fields that match the hand label, divided by the number of fields. A field counts only when it is
    labelled: an explicit null in the label ("not stated") matches a null answer."""
    return sum(f in truth and _norm(pred.get(f)) == _norm(truth[f]) for f in FIELDS) / len(FIELDS)


def hallucinations(pred: dict, truth: dict) -> list[str]:
    """Fields the label says are not stated, but the model filled in anyway."""
    return [f for f in FIELDS if f in truth and truth[f] is None and _norm(pred.get(f)) is not None]


def parse_json(text: str | None) -> dict:
    """The model's answer as a dict: tolerant of a ```json fence around it; {} when it isn't JSON."""
    if not text:
        return {}
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    try:
        out = json.loads(m.group(1) if m else text)
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


def load_cases(path: str | Path = CASES) -> list[dict]:
    return [json.loads(l) for l in Path(path).read_text(encoding="utf-8").splitlines() if l.strip()]


def evaluate(endpoint: str, cases: Iterable[dict], ask: Callable[..., Reply] = chat) -> dict:
    """Run every case on one endpoint: the metrics, plus one row per case. No MLflow here, so it is unit-tested."""
    rows, tin, tout, lat = [], 0, 0, 0.0
    for c in cases:
        r = ask(c["description"], endpoint, SYSTEM)
        pred = parse_json(r.text)
        rows.append({"id": c.get("id") or c.get("di"), "accuracy": score(pred, c["expected"]),
                     "hallucinated": hallucinations(pred, c["expected"]), "parsed": bool(pred),
                     "prediction": pred})
        tin += r.prompt_tokens
        tout += r.completion_tokens
        lat += r.latency_s
    n = max(len(rows), 1)
    return {"metrics": {"accuracy": sum(x["accuracy"] for x in rows) / n,
                        "json_parsed": sum(x["parsed"] for x in rows) / n,
                        "hallucinations": sum(len(x["hallucinated"]) for x in rows),
                        "tokens_in": tin, "tokens_out": tout, "latency_avg_s": lat / n},
            "rows": rows}


def cost_per_1000(tokens_in: int, tokens_out: int, n_cases: int, usd_per_m_in: float, usd_per_m_out: float) -> float:
    """Cost to read 1,000 gloves, from the measured tokens and the endpoint's pay-per-token prices (USD per million
    tokens; look them up on the pricing page, they change)."""
    per_case = (tokens_in * usd_per_m_in + tokens_out * usd_per_m_out) / 1e6 / max(n_cases, 1)
    return round(per_case * 1000, 4)


def run(endpoint: str, cases: list[dict], ask: Callable[..., Reply] = chat) -> dict:
    """One MLflow run per endpoint, so the experiment compares the models side by side."""
    import mlflow
    with mlflow.start_run(run_name=endpoint):
        result = evaluate(endpoint, cases, ask)
        mlflow.log_params({"endpoint": endpoint, "n": len(cases), "fields": ",".join(FIELDS), "temperature": 0})
        mlflow.log_metrics(result["metrics"])
        mlflow.log_dict({"rows": result["rows"]}, "per_case.json")
    return result
