"""Ensure Spark workers receive the QuoteAI custom data source package."""

import ast
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_bundle_builds_quote_ai_wheel() -> None:
    """Build the package artifact used by serverless job environments."""
    config = yaml.safe_load((ROOT / "databricks.yml").read_text())
    artifact = config["artifacts"]["default"]
    assert artifact["type"] == "whl"
    assert artifact["path"] == "."
    assert artifact["build"] == "python -m pip wheel . --no-deps --wheel-dir dist"


@pytest.mark.parametrize("schedule", ["once", "daily", "on_demand"])
def test_job_environment_installs_package_for_workers(schedule: str) -> None:
    """Install the wheel rather than relying solely on the driver's sys.path."""
    path = ROOT / f"resources/quote_ai/quote_ai_ingest_{schedule}.job.yml"
    job = yaml.safe_load(path.read_text())["resources"]["jobs"][f"quote_ai_ingest_{schedule}"]
    environments = {environment["environment_key"]: environment["spec"] for environment in job["environments"]}
    for task in job["tasks"]:
        if "spark_python_task" in task:
            assert "../../dist/*.whl" in environments[task["environment_key"]]["dependencies"]


def test_generator_preserves_worker_package_dependency() -> None:
    """Keep generated job environments consistent with the checked-in resources."""
    module = ast.parse((ROOT / "src/quote_ai/ingest/bundle_gen.py").read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "job")
    assert any(isinstance(node, ast.Constant) and node.value == "../../dist/*.whl" for node in ast.walk(function))