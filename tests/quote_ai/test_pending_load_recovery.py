"""Test manifested-load recovery without requiring Spark or model dependencies."""

import __future__
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src/quote_ai/ingest"


def load_definitions(filename: str, names: set[str], namespace: dict) -> dict:
    """Load selected definitions while leaving optional runtime imports isolated."""
    module = ast.parse((SOURCE_ROOT / filename).read_text())
    nodes = [node for node in module.body if isinstance(node, (ast.ClassDef, ast.FunctionDef)) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def test_manifest_pending_ids_exclude_only_completed_source_runs() -> None:
    """Keep failed and running loads pending without borrowing another source's success."""
    runtime = load_definitions("log.py", {"InMemoryLog"}, {})
    log = runtime["InMemoryLog"]()
    log.file_rows = [SimpleNamespace(source="daily", load_id=load_id) for load_id in ("old", "done", "old")]
    log.runs = {
        "failed": SimpleNamespace(source="daily", status="failed", load_ids=["old"]),
        "running": SimpleNamespace(source="daily", status="running", load_ids=["old"]),
        "other": SimpleNamespace(source="other", status="succeeded", load_ids=["old"]),
        "done": SimpleNamespace(source="daily", status="succeeded", load_ids=["done"]),
    }
    assert log.pending_load_ids("daily") == ["old"]


@pytest.mark.parametrize("pipeline_state", ["SUCCESS", "FAILED"])
def test_old_manifest_loads_wait_for_pipeline_outcome(pipeline_state: str) -> None:
    """A new run tracks old pending IDs even when no new downloads are needed."""
    now = datetime.now(timezone.utc)
    rec = SimpleNamespace(status="running", load_ids=[], rows_by_table={}, ended_at=None)
    runtime = load_definitions("engine.py", {"land", "record"}, {
        "Http": Mock(), "new_run": Mock(return_value=rec), "run_id_for": lambda *args: "new:daily",
        "land_source": Mock(return_value=[]), "utcnow": lambda: now, "SUCCESS_STATES": {"SUCCESS"},
    })
    source = SimpleNamespace(name="daily", type="file", volume="bronze.releases",
                             tables=[SimpleNamespace(target="bronze.daily")])
    cfg = SimpleNamespace(enabled=lambda **kwargs: [source])
    names = SimpleNamespace(volume_path=lambda value: value, table=lambda value: value)
    log = Mock()
    log.pending_load_ids.return_value = ["daily:old"]
    log.get.return_value = rec
    ctx = SimpleNamespace(job_run_id="new")

    assert runtime["land"](cfg, "daily", names, log, ctx, now.date()) == {"daily": ["daily:old"]}
    assert rec.status == "running"
    io = Mock()
    io.count_by_load_ids.return_value = 4
    failed = runtime["record"](cfg, "daily", names, log, io, ctx, pipeline_state)

    assert rec.status == ("succeeded" if pipeline_state == "SUCCESS" else "failed")
    assert failed == ([] if pipeline_state == "SUCCESS" else ["daily"])
    if pipeline_state == "SUCCESS":
        io.count_by_load_ids.assert_called_once_with("bronze.daily", ["daily:old"])
    else:
        io.count_by_load_ids.assert_not_called()


def test_delta_pending_query_is_source_scoped_and_requires_success() -> None:
    """Use the same manifest-minus-completed rule in the Delta-backed writer."""
    runtime = load_definitions("spark_io.py", {"DeltaLog"}, {"qualified": lambda value: value, "_lit": repr})
    io = Mock()
    io.query.return_value = [("daily:old",)]
    assert runtime["DeltaLog"](io, "catalog.ops").pending_load_ids("daily") == ["daily:old"]
    query = io.query.call_args.args[0]
    assert "LEFT ANTI JOIN" in query
    assert "WHERE source = 'daily' AND status IN ('succeeded', 'partial')" in query
    assert "WHERE f.source = 'daily'" in query