"""Prove category gating happens before API reads while raw ingestion stays broad."""

from datetime import date
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from quote_ai.ingest import bundle_gen, engine
from quote_ai.ingest.classification import classification_digest
from quote_ai.ingest.config import IngestConfig, load_config
from quote_ai.ingest.log import InMemoryLog, RunContext, new_run, utcnow
from quote_ai.ingest.log import config_rows

ROOT = Path(__file__).resolve().parents[2]
TODAY = date(2026, 11, 18)
NAMES = engine.Names(catalog="example", schemas={"quote_ai_ops": "custom_ops"})
CONTEXT = RunContext(job_run_id="filtered")
API_SOURCE = "gudid_api_device_history"


def runtime_io(candidates: list[tuple]) -> Mock:
    """Return clean snapshot metadata, then simulated SQL-filtered candidates."""
    io = Mock()
    io.table_exists.return_value = True
    io.query.side_effect = [[(0,)], candidates]
    io.merge.return_value = {"read": 1, "inserted": 1, "updated": 0}
    return io


def test_no_matching_category_issues_no_api_requests() -> None:
    """Unknown and excluded inputs do not fall back to a broad API scan."""
    cfg, log, io = load_config(), InMemoryLog(), runtime_io([])
    record = engine.run_api(cfg, API_SOURCE, NAMES, log, io, CONTEXT, TODAY)

    assert record.status == "no_new_files"
    io.read_api.assert_not_called()
    io.merge.assert_not_called()
    assert "EXISTS" in io.query.call_args.args[0]
    assert "category.decision = 'included'" in io.query.call_args.args[0]


def test_category_selection_precedes_cap_and_ignores_old_release_watermark() -> None:
    """Newly classified old releases are selectable without reopening the broad API path."""
    cfg, log = load_config(), InMemoryLog()
    cfg.source(API_SOURCE).api.max_requests_per_run = 1
    old = new_run("previous", API_SOURCE, "api", [], CONTEXT)
    old.status, old.watermark_to, old.ended_at = "succeeded", "2026-11-17", utcnow()
    log.finish(old)
    io = runtime_io([("glove-older", date(2026, 11, 10)), ("glove-newer", TODAY)])
    record = engine.run_api(cfg, API_SOURCE, NAMES, log, io, CONTEXT, TODAY)

    assert io.read_api.call_args.args[0]["ids"] == "glove-older"
    assert record.status == "partial"
    query = io.query.call_args.args[0]
    assert "`example`.`custom_ops`.`glove_classification`" in query
    assert f"category.classification_hash = '{classification_digest(cfg.classification)}'" in query
    assert "category.source_release = s.`_release_date`" in query
    assert "category.source_load_id = s.`_load_id`" in query
    assert "category.public_version_number <=> CAST(s.`public_version_number` AS STRING)" in query
    assert "s.`_release_date` > DATE" not in query
    assert "LEFT ANTI JOIN" in query


@pytest.mark.parametrize("missing", [True, False])
def test_missing_or_stale_snapshot_fails_before_network(missing: bool) -> None:
    """A filter configuration cannot silently bypass a missing or stale snapshot."""
    cfg, log, io = load_config(), InMemoryLog(), Mock()
    io.table_exists.return_value = not missing
    io.query.return_value = [(1,)]
    with pytest.raises(RuntimeError, match="missing|rules changed"):
        engine.run_api(cfg, API_SOURCE, NAMES, log, io, CONTEXT, TODAY)
    io.read_api.assert_not_called()
    assert log.get("filtered:gudid_api_device_history").status == "failed"


def test_daily_generation_orders_classification_before_api() -> None:
    """Use a separate task after file completion and before API enrichment."""
    cfg = load_config()
    tasks = {task["task_key"]: task for task in bundle_gen.job(cfg, "daily")["tasks"]}
    assert tasks["classify_gloves"]["depends_on"] == [{"task_key": "record"}]
    assert tasks[f"api_{API_SOURCE}"]["depends_on"] == [{"task_key": "classify_gloves"}]
    assert tasks["classify_gloves"]["spark_python_task"]["parameters"][0] == "classify"
    assert "classify_gloves" not in {task["task_key"] for task in bundle_gen.job(cfg, "on_demand")["tasks"]}
    cfg.classification = None
    assert "classify_gloves" not in {task["task_key"] for task in bundle_gen.job(cfg, "daily")["tasks"]}


@pytest.mark.parametrize("mutation", ["unknown", "parameter", "column", "daily", "reviewer"])
def test_invalid_filter_contract_is_rejected(mutation: str) -> None:
    """Reject wrong source bindings and verified rules without reviewer provenance."""
    raw = load_config().model_dump(mode="json")
    api = next(source for source in raw["sources"] if source["name"] == API_SOURCE)
    if mutation == "unknown":
        raw["classification"]["daily_source"] = "missing"
    elif mutation == "parameter":
        api["ids_from"]["param"] = "di"
    elif mutation == "column":
        api["ids_from"]["column"] = "other_key"
    elif mutation == "daily":
        api["ids_from"]["table"] = "quote_ai_gudid_bronze.device_raw"
    else:
        raw["classification"]["rules"][0]["reviewed_by"] = None
    with pytest.raises(ValidationError):
        IngestConfig.model_validate(raw)


def test_raw_device_pattern_does_not_match_size_entity() -> None:
    """Keep raw ingestion complete but separate entity layouts before classifying."""
    from fnmatch import fnmatchcase

    pattern = load_config().source("gudid_full_release").tables[0].pattern
    assert fnmatchcase("device.txt", pattern)
    assert not fnmatchcase("deviceSizes.txt", pattern)


def test_config_history_preserves_rules_for_category_decisions() -> None:
    """Audit entries capture the exact policy without changing unrelated source entries."""
    cfg = load_config()
    entries = {row["source"]: json.loads(row["entry_json"]) for row in config_rows(cfg, "hash", "commit")}
    assert entries[API_SOURCE]["classification"] == cfg.classification.model_dump(mode="json")
    assert entries["gudid_daily_updates"]["classification"]["version"] == cfg.classification.version
    assert "classification" not in entries["dbxlab_synthetic"]