"""Check full-release selection without optional model or Spark dependencies."""

import ast
import __future__
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 17)


@pytest.fixture
def runtime() -> dict:
    """Load only the landing dispatcher with a mocked release downloader."""
    module = ast.parse((ROOT / "src/quote_ai/ingest/download.py").read_text())
    function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "land_source")
    namespace = {"land_release": Mock(), "timedelta": timedelta}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "download.py", "exec",
                 flags=__future__.annotations.compiler_flag), namespace)
    return namespace


def test_blank_date_uses_latest_available_release(runtime: dict) -> None:
    """Search newest-first and stop at the first published release."""
    source = SimpleNamespace(name="full", generator=None, download=SimpleNamespace(kind="full", lookback_days=2))
    runtime["land_release"].side_effect = [None, ["record"]]
    assert runtime["land_source"]("http", source, "root", "log", TODAY) == ["record"]
    assert runtime["land_release"].call_args_list == [
        call("http", source, TODAY, "root", "log", None),
        call("http", source, TODAY - timedelta(days=1), "root", "log", None),
    ]


def test_explicit_date_is_not_replaced(runtime: dict) -> None:
    """An explicit missing release fails without silently choosing another date."""
    source = SimpleNamespace(name="full", generator=None, download=SimpleNamespace(kind="full", lookback_days=2))
    runtime["land_release"].return_value = None
    with pytest.raises(ValueError, match="no full release published"):
        runtime["land_source"]("http", source, "root", "log", TODAY, release_date=TODAY)
    runtime["land_release"].assert_called_once()


def test_missing_releases_stop_at_configured_boundary(runtime: dict) -> None:
    """Bound the search and give an actionable error when no baseline exists."""
    source = SimpleNamespace(name="full", generator=None, download=SimpleNamespace(kind="full", lookback_days=2))
    runtime["land_release"].return_value = None
    with pytest.raises(ValueError, match="release_date"):
        runtime["land_source"]("http", source, "root", "log", TODAY)
    assert runtime["land_release"].call_count == 3


def test_full_release_config_covers_monthly_publication() -> None:
    """Allow enough history to find the previous monthly full release."""
    config = yaml.safe_load((ROOT / "config/quote_ai/ingest/sources.yml").read_text())
    source = next(source for source in config["sources"] if source["name"] == "gudid_full_release")
    assert source["download"]["lookback_days"] == 60