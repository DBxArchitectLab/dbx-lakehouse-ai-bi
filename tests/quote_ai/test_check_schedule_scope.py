"""Validate schedule-scoped checks without optional runtime dependencies."""

import ast
import __future__
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("schedule", [None, "once", "daily", "on_demand"])
def test_checks_select_only_the_requested_schedule(schedule: str) -> None:
    """Keep lineage checks strict while excluding unrelated source schedules."""
    document = yaml.safe_load((ROOT / "config/quote_ai/ingest/sources.yml").read_text())
    sources = [SimpleNamespace(
        schedule=source["schedule"],
        tables=[SimpleNamespace(target=table["target"], keys=table["keys"]) for table in source["tables"]],
    ) for source in document["sources"] if source.get("enabled", True)]
    config = SimpleNamespace(enabled=lambda schedule=None: [
        source for source in sources if schedule is None or source.schedule == schedule
    ])
    module = ast.parse((ROOT / "src/quote_ai/ingest/checks.py").read_text())
    nodes = [node for node in module.body if isinstance(node, (ast.ClassDef, ast.FunctionDef))]
    namespace = {"dataclass": dataclass, "qualified": lambda value: value}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "checks.py", "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    checks = namespace["bronze_checks"](config, lambda value: value, "catalog.ops", schedule=schedule)
    expected = {table.target for source in config.enabled(schedule=schedule) for table in source.tables}

    assert {check.table for check in checks} == expected
    assert len(checks) == 3 * len(expected)
    assert all("status IN ('succeeded', 'partial')" in check.sql for check in checks if check.name == "load_id_logged")


@pytest.mark.parametrize("schedule", ["once", "daily", "on_demand"])
def test_deployed_check_tasks_pass_the_job_schedule(schedule: str) -> None:
    """Wire the schedule into each checked-in generated job's checks task."""
    document = yaml.safe_load((ROOT / f"resources/quote_ai/quote_ai_ingest_{schedule}.job.yml").read_text())
    job = document["resources"]["jobs"][f"quote_ai_ingest_{schedule}"]
    task = next(task for task in job["tasks"] if task["task_key"] == "checks")
    assert task["spark_python_task"]["parameters"][:3] == ["checks", "--schedule", schedule]