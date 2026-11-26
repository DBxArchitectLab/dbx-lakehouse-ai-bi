"""Watching the win model (AI-14): inputs, predictions and, once outcomes arrive, real accuracy.

Data and model quality monitoring (formerly Lakehouse Monitoring) runs an inference profile on
quote_ai_ml.open_quote_scores: the prediction column, the label once the quote is decided, the scoring timestamp,
the model version, daily; its metric tables and dashboard land in the quote_ai_monitoring schema. Uptime and latency
alone say nothing about whether the model is still right.

drift() is the same idea in plain Python, used by the drift demo and the tests: the population stability index
(PSI) of a column between a reference window and the current one. 0.1 to 0.2 is a shift worth a look; above 0.2 is
drift. AI-14's injected drift (quote_ai.mock.generate_dbxlab.drift_month: customers' current prices fall) shows up
in price_vs_customer_current_pct and in the prediction distribution.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence

DRIFT_PSI = 0.2
WATCHED = ("price_vs_customer_current_pct", "lead_time_days", "stock_cover_days", "win_rate_12m", "prediction")


def psi(reference: Sequence[float], current: Sequence[float], bins: int = 10) -> float:
    """Population stability index over the reference's quantile bins (a small floor avoids log(0))."""
    ref = sorted(x for x in reference if x is not None)
    cur = [x for x in current if x is not None]
    if not ref or not cur:
        raise ValueError("PSI needs values in both windows")
    edges = sorted({ref[min(int(len(ref) * i / bins), len(ref) - 1)] for i in range(1, bins)})

    def shares(xs):
        counts = [0] * (len(edges) + 1)
        for x in xs:
            counts[sum(1 for e in edges if x >= e)] += 1
        return [max(c / len(xs), 1e-4) for c in counts]
    return sum((c - r) * math.log(c / r) for r, c in zip(shares(ref), shares(cur)))


def drift(reference: Iterable[Mapping], current: Iterable[Mapping], columns: Sequence[str] = WATCHED,
          threshold: float = DRIFT_PSI) -> dict[str, dict]:
    reference, current = list(reference), list(current)
    out = {}
    for c in columns:
        value = psi([r.get(c) for r in reference], [r.get(c) for r in current])
        out[c] = {"psi": round(value, 4), "drift": value > threshold}
    return out


# ------------------------------------------------------------------ Databricks

def create_or_update_monitor(table: str, output_schema: str, assets_dir: str, baseline_table: str | None = None):
    """The inference monitor on the score table. Verify before recording: the current name and SDK of data and model
    quality monitoring (w.quality_monitors here), MonitorInferenceLog's fields, and the cron schedule format."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    from databricks.sdk.service.catalog import (MonitorCronSchedule, MonitorInferenceLog,
                                                MonitorInferenceLogProblemType)
    w = WorkspaceClient()
    spec = dict(
        output_schema_name=output_schema,
        inference_log=MonitorInferenceLog(problem_type=MonitorInferenceLogProblemType.PROBLEM_TYPE_CLASSIFICATION,
                                          prediction_col="prediction", label_col="won", timestamp_col="scored_at",
                                          model_id_col="model_version", granularities=["1 day"]),
        schedule=MonitorCronSchedule(quartz_cron_expression="0 30 4 * * ?", timezone_id="UTC"),
        baseline_table_name=baseline_table)
    try:
        w.quality_monitors.get(table)
        return w.quality_monitors.update(table_name=table, **spec)
    except NotFound:
        return w.quality_monitors.create(table_name=table, assets_dir=assets_dir, **spec)


def refresh(table: str):
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient().quality_monitors.run_refresh(table_name=table)
