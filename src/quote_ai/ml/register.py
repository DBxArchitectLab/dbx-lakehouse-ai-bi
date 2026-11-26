"""Register, alias, promote (AI-14): the selected run becomes a governed model in Unity Catalog.

  quote_ai_ml.win_model   versions, a signature, and two aliases:
    @challenger  every newly registered version, straight away
    @champion    the version in production: jobs and the endpoint use it; moved only by this job, only when the
                 challenger beats the champion on the latest month of decided quotes
Promotion is a change of alias made by a reviewed job, audited in quote_ai_ml.model_promotion; never a code change,
never a manual click in prod, and never a model loaded from a run ID in production code.

The decision (should_promote) is plain Python, tested in tests/quote_ai/test_score.py; MLflow, Feature Engineering
and the SDK are imported lazily in the job (src/quote_ai/jobs/ml_job.py register).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime

CHAMPION, CHALLENGER = "champion", "challenger"
MIN_GAIN = 0.0          # the challenger must be strictly better than the champion on the latest month


def model_name(names: Mapping[str, str]) -> str:
    return f"{names['ml']}.win_model"


def alias_uri(name: str, alias: str = CHAMPION) -> str:
    """models:/catalog.schema.win_model@champion: what jobs, the endpoint's version and the app follow."""
    if alias not in (CHAMPION, CHALLENGER):
        raise ValueError(f"unknown alias {alias!r}")
    return f"models:/{name}@{alias}"


def should_promote(challenger_auc: float, champion_auc: float | None, min_gain: float = MIN_GAIN) -> tuple[bool, str]:
    """Promote only a challenger that beats the champion on the latest month. No champion yet: the first selected
    version (it already passed the selection rule of AI-13) becomes champion."""
    if champion_auc is None:
        return True, f"no champion yet: the selected version becomes champion (AUC {challenger_auc:.3f})"
    if challenger_auc > champion_auc + min_gain:
        return True, f"challenger AUC {challenger_auc:.3f} beats champion {champion_auc:.3f} on the latest month"
    return False, (f"challenger AUC {challenger_auc:.3f} does not beat champion {champion_auc:.3f} on the latest "
                   f"month: champion stays")


def latest_month(rows: Iterable[Mapping], ts: str = "sent_at") -> list[dict]:
    """The decided quotes of the most recent calendar month in the labels: where a challenger has to prove itself."""
    rows = list(rows)
    if not rows:
        return []
    last = max(r[ts] for r in rows)
    return [dict(r) for r in rows if (r[ts].year, r[ts].month) == (last.year, last.month)]


def grant_statements(name: str, principals: Iterable[str]) -> list[str]:
    """EXECUTE on the model for the app and agent identities only. Verify before recording: GRANT EXECUTE ON MODEL
    syntax for Unity Catalog registered models."""
    return [f"GRANT EXECUTE ON MODEL {name} TO `{p}`" for p in principals if p]


# ------------------------------------------------------------------ Databricks (the register job)

def register(run_id: str, name: str):
    """Register runs:/<id>/model as a new version and give it @challenger. Verify: mlflow.register_model with the
    databricks-uc registry for a Feature Engineering packaged model."""
    import mlflow
    mlflow.set_registry_uri("databricks-uc")
    mv = mlflow.register_model(f"runs:/{run_id}/model", name)
    mlflow.MlflowClient().set_registered_model_alias(name, CHALLENGER, mv.version)
    return mv


def alias_version(name: str, alias: str = CHAMPION) -> str | None:
    import mlflow
    from mlflow.exceptions import MlflowException
    mlflow.set_registry_uri("databricks-uc")
    try:
        return str(mlflow.MlflowClient().get_model_version_by_alias(name, alias).version)
    except MlflowException:
        return None


def set_champion(name: str, version: str) -> None:
    import mlflow
    mlflow.set_registry_uri("databricks-uc")
    mlflow.MlflowClient().set_registered_model_alias(name, CHAMPION, version)


def point_endpoint(endpoint: str, name: str, version: str) -> None:
    """Serve the champion's version on the endpoint (the bundle declares it; this job keeps it on @champion, so the
    endpoint is never pointed at a version by hand). Verify: serving_endpoints.update_config with served_entities on
    the current SDK, and whether the endpoint can follow an alias directly."""
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.service.serving import ServedEntityInput
    w = WorkspaceClient()
    current = w.serving_endpoints.get(endpoint)
    entity = current.config.served_entities[0]
    w.serving_endpoints.update_config(endpoint, served_entities=[ServedEntityInput(
        entity_name=name, entity_version=version, workload_size=entity.workload_size,
        scale_to_zero_enabled=entity.scale_to_zero_enabled)])


def promotion_row(version: str, challenger_auc: float, champion_version: str | None, champion_auc: float | None,
                  promoted: bool, reason: str, run_id: str, at: datetime) -> dict:
    return {"decided_at": at, "model_version": version, "run_id": run_id, "challenger_auc": challenger_auc,
            "champion_version": champion_version, "champion_auc": champion_auc, "promoted": promoted,
            "reason": reason}
