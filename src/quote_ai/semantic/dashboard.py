"""The sales overview dashboard as code (AI-10): its lint, and where the app finds it to embed.

  lint(dashboard)    what CI checks on src/quote_ai/semantic/sales_overview.lvdash.json: valid JSON, every dataset
                     queries a metric view with MEASURE(), and no hard-coded catalog or schema (the bundle sets
                     dataset_catalog / dataset_schema per target). A dataset that re-implements win rate fails.
  embed_info(id)     the embed URL of the published dashboard for the My pipeline page, in one adapter
"""
from __future__ import annotations

import json
import re
from pathlib import Path

PATH = Path(__file__).resolve().parent / "sales_overview.lvdash.json"
METRIC_VIEWS = ("quote_metrics", "order_metrics")
DISPLAY_NAME = "QuoteAI - Sales overview"
_FROM = re.compile(r"\b(?:FROM|JOIN)\s+([`\w.]+)", re.I)
# a measure computed by hand instead of read with MEASURE(): the "second definition" the lint exists to catch
_HAND_MADE = re.compile(r"\b(COUNT_IF|COUNT|SUM|AVG|MEDIAN)\s*\(", re.I)


def load(path: str | Path = PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def query(dataset: dict) -> str:
    return "".join(dataset.get("queryLines") or [dataset.get("query", "")])


def lint(dashboard: dict) -> list[str]:
    """The problems, one line each; [] when the dashboard is fine."""
    errors = []
    datasets = dashboard.get("datasets") or []
    if not datasets:
        errors.append("no datasets")
    for d in datasets:
        q, name = query(d), d.get("name", "?")
        tables = [t.strip("`") for t in _FROM.findall(q)]
        if not tables or any(t not in METRIC_VIEWS for t in tables):
            errors.append(f"{name}: queries {tables or 'nothing'}, not only the metric views {METRIC_VIEWS}")
        if any("." in t for t in tables):
            errors.append(f"{name}: a qualified name; catalog and schema come from the bundle")
        if "MEASURE(" not in q.upper():
            errors.append(f"{name}: no MEASURE(): a measure must come from the metric view")
        if _HAND_MADE.search(re.sub(r"MEASURE\s*\([^)]*\)", "", q, flags=re.I)):
            errors.append(f"{name}: an aggregate written by hand; use the metric view's measure")
    names = {d.get("name") for d in datasets}
    for page in dashboard.get("pages", []):
        for item in page.get("layout", []):
            for qry in item.get("widget", {}).get("queries", []):
                ds = qry.get("query", {}).get("datasetName")
                if ds and ds not in names:
                    errors.append(f"widget {item['widget'].get('name')}: unknown dataset {ds}")
    return errors


def rep_id_for(user_id: str) -> str:
    """The signed-in user (rep.alice@dbxlab.example) as the rep dimension's value (rep.alice)."""
    return user_id.split("@", 1)[0]


def embed_info(dashboard_id: str | None = None) -> dict:
    """{"url", "dashboard_id", "reason"} for the My pipeline iframe. The dashboard ID comes from QUOTE_AI_DASHBOARD_ID,
    else the published dashboard is found by its display name (a personal dev copy is prefixed "[dev <user>]").
    Verify before recording: the embed URL form (/embed/dashboardsv3/<id>), the app's domain in the workspace's
    allowed embedding domains, and that the app's service principal can list the dashboard (CAN_READ)."""
    try:
        from databricks.sdk import WorkspaceClient
        w = WorkspaceClient()
        if not dashboard_id:
            found = [d for d in w.lakeview.list() if (d.display_name or "").endswith(DISPLAY_NAME)]
            if not found:
                return {"url": None, "dashboard_id": None, "reason": f"no dashboard named {DISPLAY_NAME!r}"}
            dashboard_id = found[0].dashboard_id
        return {"url": f"{w.config.host.rstrip('/')}/embed/dashboardsv3/{dashboard_id}",
                "dashboard_id": dashboard_id, "reason": ""}
    except Exception as e:  # noqa: BLE001  the page explains instead of failing
        return {"url": None, "dashboard_id": dashboard_id, "reason": f"dashboard not reachable: {e}"}
