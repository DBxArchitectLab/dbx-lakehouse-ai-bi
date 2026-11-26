"""The release rule (AI-22): one thresholds file, one verdict. Plain Python, unit-tested (tests/quote_ai/test_gate.py).

  evaluation/quote_ai/v1/thresholds.yml   every suite of the package: its episode, whether it is required for a
                                          release, where it runs, and a threshold per metric
  evaluation/quote_ai/v1/VERSION          the package version written with every result row

A threshold is {max: n} (a gate: 0 for anything that must never happen), {min: x} (a metric that must not drop) or
{equals: v}. A threshold with `needs: judge | review | workspace` is checked only where that input exists: the eval
job in dev-shared has the judge and the review labels, a pull request's CI step does not, so there it is "not run",
never a silent pass. A required suite with a failing threshold, or a missing metric it should have produced, fails the
release; the runner then exits 1.

Changing the release rule is changing this file: a reviewed pull request.
"""
from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
PACKAGE = HERE / "v1"
RUNS = ("ci", "workspace", "separate", "monitored")


def package_version(package: Path = PACKAGE) -> str:
    return (package / "VERSION").read_text(encoding="utf-8").strip()


def load_thresholds(path: Path | None = None) -> dict:
    """The suites from thresholds.yml, checked: every suite has an episode, a run place and thresholds."""
    import yaml
    doc = yaml.safe_load((path or PACKAGE / "thresholds.yml").read_text(encoding="utf-8"))
    suites = doc["suites"]
    for name, s in suites.items():
        if s.get("runs") not in RUNS:
            raise ValueError(f"{name}: runs must be one of {RUNS}")
        if not s.get("episode") or "required" not in s:
            raise ValueError(f"{name}: needs episode and required")
        for metric, t in (s.get("thresholds") or {}).items():
            if not ({"max", "min", "equals"} & set(t)):
                raise ValueError(f"{name}.{metric}: a threshold needs max, min or equals")
    return suites


def select(suites: dict, scope: str) -> list[str]:
    """Which suites a run covers. ci: the pure-code suites (every pull request); all: every suite the eval job can
    run (ci + workspace); required: the required ones; or a comma list of names."""
    if scope == "ci":
        return [n for n, s in suites.items() if s["runs"] == "ci"]
    if scope == "all":
        return [n for n, s in suites.items() if s["runs"] in ("ci", "workspace")]
    if scope == "required":
        return [n for n, s in suites.items() if s["required"]]
    names = [n.strip() for n in scope.split(",") if n.strip()]
    unknown = set(names) - set(suites)
    if unknown:
        raise ValueError(f"unknown suites: {sorted(unknown)}")
    return names


def _ok(value, t: dict) -> bool:
    if "max" in t and not value <= t["max"]:
        return False
    if "min" in t and not value >= t["min"]:
        return False
    return not ("equals" in t and value != t["equals"])


def check(suite: str, spec: dict, metrics: dict, have: frozenset[str] = frozenset()) -> dict:
    """One suite's metrics against its thresholds. `have` names the optional inputs this run has (judge, review,
    workspace). Returns {"suite", "passed", "checks": [{metric, value, threshold, status}]}, status pass, fail or
    not_run (an input this run doesn't have); a metric missing although its input is there is a fail."""
    rows = []
    for metric, t in (spec.get("thresholds") or {}).items():
        need = t.get("needs")
        limit = {k: v for k, v in t.items() if k in ("max", "min", "equals")}
        if need and need not in have:
            rows.append({"metric": metric, "value": metrics.get(metric), "threshold": limit, "status": "not_run",
                         "needs": need})
            continue
        if metrics.get(metric) is None:
            rows.append({"metric": metric, "value": None, "threshold": limit, "status": "fail",
                         "reason": "not produced"})
            continue
        rows.append({"metric": metric, "value": metrics[metric], "threshold": limit,
                     "status": "pass" if _ok(metrics[metric], t) else "fail"})
    return {"suite": suite, "required": spec["required"], "passed": all(r["status"] != "fail" for r in rows),
            "checks": rows}


def verdict(results: list[dict]) -> bool:
    """The release passes when no required suite failed. A declared, not-yet-required suite is reported only."""
    return all(r["passed"] for r in results if r["required"])


def git_sha() -> str:
    sha = os.environ.get("GITHUB_SHA") or os.environ.get("QUOTE_AI_GIT_SHA")
    if sha:
        return sha
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE,
                              check=True).stdout.strip()
    except Exception:  # noqa: BLE001  a run outside a checkout (a workspace job from a wheel)
        return "unknown"


def result_rows(results: list[dict], run_id: str | None = None, sha: str | None = None,
                version: str | None = None, costs: dict | None = None, at: datetime | None = None) -> list[dict]:
    """One row per suite and metric, for {eval}.eval_results: the history of every run (git SHA, package version,
    the model calls and cost of the judge suites)."""
    run_id = run_id or uuid.uuid4().hex[:12]
    sha, version = sha or git_sha(), version or package_version()
    at = at or datetime.now(timezone.utc)
    out = []
    for r in results:
        cost = (costs or {}).get(r["suite"], {})
        for c in r["checks"]:
            v = c["value"]
            out.append({"run_id": run_id, "suite": r["suite"], "metric": c["metric"],
                        "value": float(v) if isinstance(v, (int, float)) else None,
                        "threshold": str(c["threshold"]), "status": c["status"], "required": r["required"],
                        "git_sha": sha, "package_version": version, "model_calls": cost.get("model_calls"),
                        "cost_usd": cost.get("cost_usd"), "run_at": at})
    return out


def write_results(rows: list[dict], table: str) -> None:
    """Append the rows to the results table (the eval job, on Databricks). Verify before recording: the Spark
    session of a serverless job task and the column types (eval_tables.sql)."""
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    if rows:
        spark.createDataFrame(rows, spark.table(table).schema).write.mode("append").insertInto(table)


def table(results: list[dict]) -> str:
    """The suite table the job log and the PR check print: suite, score, threshold, status."""
    lines = [f"{'suite':20} {'metric':32} {'value':>10}  {'threshold':16} status"]
    for r in results:
        for c in r["checks"]:
            v = c["value"]
            shown = "-" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))
            lines.append(f"{r['suite']:20} {c['metric']:32} {shown:>10}  {str(c['threshold']):16} {c['status']}"
                         + ("" if r["required"] else "  (declared)"))
    return "\n".join(lines)
