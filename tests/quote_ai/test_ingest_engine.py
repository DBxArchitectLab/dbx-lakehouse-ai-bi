"""The engine and the run log: every run logged (failures too), watermarks, the request cap, and the bundle it
generates. Spark is replaced by FakeIO: the decisions are tested here, the Spark calls in the workspace."""
from datetime import date
from pathlib import Path

import pytest

from quote_ai.ingest import bundle_gen, engine
from quote_ai.ingest.checks import bronze_checks
from quote_ai.ingest.config import load_config
from quote_ai.ingest.log import InMemoryLog, RunContext, config_rows, logged_run, new_run

ROOT = Path(__file__).resolve().parents[2]
CFG = load_config()
NAMES = engine.Names(catalog="deepwater_tst", 
                     schemas={"quote_ai_gudid_bronze": "dev_me_quote_ai_gudid_bronze",
                              "quote_ai_ops": "dev_me_quote_ai_ops"})
CTX = RunContext(job_id="11", job_run_id="901", run_as="sp-dbx-lakehouse", bundle_target="dev-shared",
                 git_commit="abc123", config_hash="h1")
TODAY = date(2026, 11, 14)


class FakeIO:
    def __init__(self, changed=(), fail_read=False, exists=True):
        self.changed, self.fail_read, self.exists = list(changed), fail_read, exists
        self.queries, self.reads, self.merges = [], [], []

    def table_exists(self, fqn):
        return self.exists

    def query(self, sql):
        self.queries.append(sql)
        return self.changed

    def read_api(self, options):
        if self.fail_read:
            raise RuntimeError("HttpError: gave up after 6 tries")
        self.reads.append(options)
        return "df"

    def merge(self, df, target, keys, lineage, sequence_by):
        self.merges.append((target, keys, lineage))
        n = len(self.reads[-1]["ids"].split(","))
        return {"read": n, "inserted": n, "updated": 0}

    def count_by_load_ids(self, fqn, load_ids):
        return 42 if load_ids else 0


def test_names_map_logical_to_physical():
    assert NAMES.table("quote_ai_gudid_bronze.device_raw") == \
        "deepwater_tst.dev_me_quote_ai_gudid_bronze.device_raw"
    assert NAMES.volume_path("quote_ai_gudid_bronze.quote_ai_releases").as_posix() == \
        "/Volumes/deepwater_tst/dev_me_quote_ai_gudid_bronze/quote_ai_releases"


# ------------------------------------------------------------------ the run log

def test_a_failed_run_is_still_logged_as_failed():
    log = InMemoryLog()
    rec = new_run("r1", "s", "file", ["quote_ai_gudid_bronze.t"], CTX)
    with pytest.raises(ZeroDivisionError):
        with logged_run(log, rec):
            1 / 0
    row = log.get("r1")
    assert row.status == "failed" and row.error.startswith("ZeroDivisionError") and row.ended_at
    assert row.job_run_id == "901" and row.git_commit == "abc123" and row.config_hash == "h1"


def test_land_logs_running_then_record_finishes_with_counts(monkeypatch):
    log = InMemoryLog()
    monkeypatch.setattr(engine, "land_source", lambda *a, **k: [
        type("F", (), {"load_id": "gudid_daily_updates:20261113"})()])
    landed = engine.land(CFG, "daily", NAMES, log, CTX, TODAY, http_factory=lambda: None)
    rec = log.get("901:gudid_daily_updates")
    assert landed == {"gudid_daily_updates": ["gudid_daily_updates:20261113"]} and rec.status == "running"

    assert engine.record(CFG, "daily", NAMES, log, FakeIO(), CTX, "SUCCESS") == []
    rec = log.get("901:gudid_daily_updates")
    assert rec.status == "succeeded" and rec.rows_by_table == {"quote_ai_gudid_bronze.daily_device_raw": 42}


def test_a_failed_pipeline_fails_the_file_runs(monkeypatch):
    log = InMemoryLog()
    monkeypatch.setattr(engine, "land_source", lambda *a, **k: [type("F", (), {"load_id": "x:20261113"})()])
    engine.land(CFG, "daily", NAMES, log, CTX, TODAY, http_factory=lambda: None)
    assert engine.record(CFG, "daily", NAMES, log, FakeIO(), CTX, "FAILED") == ["gudid_daily_updates"]
    assert log.get("901:gudid_daily_updates").error == "bronze pipeline update ended FAILED"


def test_nothing_new_is_no_new_files(monkeypatch):
    log = InMemoryLog()
    monkeypatch.setattr(engine, "land_source", lambda *a, **k: [])
    engine.land(CFG, "daily", NAMES, log, CTX, TODAY, http_factory=lambda: None)
    assert log.get("901:gudid_daily_updates").status == "no_new_files"


def test_a_landing_error_is_logged_and_raised(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("LandingConflict: published again")
    log = InMemoryLog()
    monkeypatch.setattr(engine, "land_source", boom)
    with pytest.raises(RuntimeError):
        engine.land(CFG, "daily", NAMES, log, CTX, TODAY, http_factory=lambda: None)
    assert log.get("901:gudid_daily_updates").status == "failed"


# ------------------------------------------------------------------ the API source

def test_api_first_run_then_the_watermark_moves_on():
    log, api = InMemoryLog(), "gudid_api_device_history"
    io = FakeIO(changed=[("rk-1", date(2026, 11, 12)), ("rk-2", date(2026, 11, 13))])
    unfiltered = CFG.model_copy(update={"classification": None})
    rec = engine.run_api(unfiltered, api, NAMES, log, io, CTX, TODAY)
    assert rec.status == "succeeded" and rec.watermark_from is None and rec.watermark_to == "2026-11-13"
    assert rec.load_ids == ["gudid_api_device_history:20261113"] and io.reads[0]["ids"] == "rk-1,rk-2"
    assert io.reads[0]["param"] == "record_key"
    assert "LEFT ANTI JOIN" in io.queries[0]          # the cache: what we already hold isn't asked again

    io2 = FakeIO(changed=[])
    rec2 = engine.run_api(unfiltered, api, NAMES, log, io2, CTX.model_copy(update={"job_run_id": "902"}), TODAY)
    assert "DATE'2026-11-13'" in io2.queries[0]
    assert rec2.status == "no_new_files" and rec2.watermark_to == "2026-11-13" and not io2.reads


def test_api_capped_run_is_partial_and_keeps_its_watermark():
    cfg = load_config()
    cfg.classification = None
    cfg.source("gudid_api_device_history").api.max_requests_per_run = 2
    io = FakeIO(changed=[(f"rk-{i}", date(2026, 11, 13)) for i in range(5)])
    rec = engine.run_api(cfg, "gudid_api_device_history", NAMES, InMemoryLog(), io, CTX, TODAY)
    assert rec.status == "partial" and rec.watermark_to is None and "3 records left" in rec.error
    assert io.reads[0]["ids"] == "rk-0,rk-1"


def test_api_failure_is_logged_with_the_error():
    log = InMemoryLog()
    with pytest.raises(RuntimeError):
        engine.run_api(CFG.model_copy(update={"classification": None}), "gudid_api_device_history", NAMES, log,
                       FakeIO(changed=[("rk-1", date(2026, 11, 13))], fail_read=True), CTX, TODAY)
    rec = log.get("901:gudid_api_device_history")
    assert rec.status == "failed" and "gave up after 6 tries" in rec.error


def test_changed_ids_sql_refuses_anything_but_a_date():
    with pytest.raises(ValueError):
        engine.changed_ids_sql("c.s.t", "id", None, "id", "2026-11-13' OR '1'='1")


# ------------------------------------------------------------------ checks, config history, the bundle

def test_every_bronze_table_has_the_three_checks():
    checks = bronze_checks(CFG, NAMES.table, "deepwater_tst.quote_ai_ops")
    tables = {t.target for s in CFG.sources for t in s.tables}
    assert {(c.table, c.name) for c in checks} == {(t, n) for t in tables
                                                    for n in ("one_row_per_key", "load_id_present", "load_id_logged")}
    dup = next(c for c in checks if c.table == "quote_ai_gudid_bronze.device_raw" and c.name == "one_row_per_key")
    assert "GROUP BY `primary_di` HAVING count(*) > 1" in dup.sql


def test_config_rows_one_per_source_and_table():
    rows = config_rows(CFG, "h1", "abc123")
    assert len(rows) == sum(len(s.tables) for s in CFG.sources)
    assert {r["config_hash"] for r in rows} == {"h1"}


def test_generated_bundle_resources_match_the_config():
    stale = bundle_gen.stale_files(CFG, ROOT / "resources" / "quote_ai")
    assert not stale, f"run python scripts/quote_ai/gen_ingest_resources.py: {stale}"


def test_the_daily_job_lands_then_loads_then_calls_the_api_then_checks():
    job = bundle_gen.job(CFG, "daily")
    order = [t["task_key"] for t in job["tasks"]]
    assert order == ["setup", "land", "files", "record", "classify_gloves", "api_gudid_api_device_history", "checks"]
    record = job["tasks"][3]
    assert record["run_if"] == "ALL_DONE"            # a failed pipeline is still recorded as failed
    assert job["schedule"]["quartz_cron_expression"] == "0 0 6 * * ?"
    assert job["schedule"]["timezone_id"] == "UTC"
    assert job["schedule"]["pause_status"] == "UNPAUSED"


def test_the_full_release_job_takes_the_release_date():
    job = bundle_gen.job(CFG, "once")
    assert job["parameters"] == [{"name": "release_date", "default": ""}] and "schedule" not in job
