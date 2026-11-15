"""The ingestion log and audit trail: what each run did, what landed, and which config was in force.

  quote_ai_ops.ingestion_log      one row per run of a source: inserted as running, finished as succeeded, failed,
                                  partial or
                         no_new_files. A crash still records "failed" with the error.
  quote_ai_ops.ingestion_file     one row per file landed (append-only): path, URL, size, MD5 and SHA-256. The same
                                  file landed
                         twice is recorded once; a re-published file with different content is refused.
  quote_ai_ops.ingestion_config   the config in force, one row per source and table, with history (SCD type 2).
  quote_ai_ops.v_ingestion_audit  a run, its files and its config entry, from one load ID.

Who started a job, and what changed in each table, stay in Databricks' own audit (system.access.audit, the job run
history, Delta table history, Unity Catalog lineage): we link to it through job_id and job_run_id, not copy it.
"""
from __future__ import annotations

import json
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterator, Literal, Protocol

from pydantic import BaseModel

from quote_ai.ingest.config import IngestConfig

Status = Literal["running", "succeeded", "failed", "partial", "no_new_files"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunContext(BaseModel):
    """Where a run comes from: the job, the identity, the code and the config. Recorded on every log row."""
    job_id: str | None = None
    job_run_id: str | None = None
    run_as: str | None = None
    bundle_target: str | None = None
    git_commit: str | None = None
    config_hash: str | None = None


class RunRecord(BaseModel):
    run_id: str
    source: str
    source_type: str
    target_tables: list[str]
    load_ids: list[str] = []
    status: Status = "running"
    started_at: datetime
    ended_at: datetime | None = None
    error: str | None = None
    rows_read: int | None = None
    rows_inserted: int | None = None
    rows_updated: int | None = None
    rows_rejected: int | None = None
    rows_by_table: dict[str, int] = {}
    watermark_from: str | None = None
    watermark_to: str | None = None
    pipeline_update_id: str | None = None
    job_id: str | None = None
    job_run_id: str | None = None
    run_as: str | None = None
    bundle_target: str | None = None
    git_commit: str | None = None
    config_hash: str | None = None


class FileRecord(BaseModel):
    load_id: str
    source: str
    kind: str
    path: str
    source_url: str | None = None
    release_date: date | None = None
    size_bytes: int
    md5: str
    sha256: str
    downloaded_at: datetime
    row_count: int | None = None
    job_run_id: str | None = None


class LogWriter(Protocol):
    def start(self, rec: RunRecord) -> None: ...
    def finish(self, rec: RunRecord) -> None: ...
    def get(self, run_id: str) -> RunRecord | None: ...
    def add_files(self, recs: list[FileRecord]) -> int: ...
    def files(self, source: str, job_run_id: str | None = None) -> list[FileRecord]: ...
    def last_success(self, source: str) -> RunRecord | None: ...


class InMemoryLog:
    """The log for tests and local runs; DeltaLog (spark_io.py) is the same contract on the ops tables."""

    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.file_rows: list[FileRecord] = []

    def start(self, rec: RunRecord) -> None:
        self.runs[rec.run_id] = rec.model_copy()

    def finish(self, rec: RunRecord) -> None:
        self.runs[rec.run_id] = rec.model_copy()

    def get(self, run_id: str) -> RunRecord | None:
        r = self.runs.get(run_id)
        return r.model_copy() if r else None

    def add_files(self, recs: list[FileRecord]) -> int:
        known = {(f.source, f.path, f.sha256) for f in self.file_rows}
        new = [r for r in recs if (r.source, r.path, r.sha256) not in known]
        self.file_rows.extend(new)
        return len(new)

    def files(self, source: str, job_run_id: str | None = None) -> list[FileRecord]:
        return [f for f in self.file_rows if f.source == source and (job_run_id is None or f.job_run_id == job_run_id)]

    def last_success(self, source: str) -> RunRecord | None:
        done = [r for r in self.runs.values() if r.source == source and r.status in ("succeeded", "partial")]
        return max(done, key=lambda r: r.ended_at or r.started_at, default=None)


def new_run(run_id: str, source: str, source_type: str, targets: list[str], ctx: RunContext,
            started_at: datetime | None = None) -> RunRecord:
    return RunRecord(run_id=run_id, source=source, source_type=source_type, target_tables=targets,
                     started_at=started_at or utcnow(), **ctx.model_dump(exclude_none=True))


@contextmanager
def logged_run(log: LogWriter, rec: RunRecord, clock=utcnow) -> Iterator[RunRecord]:
    """Insert the run as running; finish it as succeeded (or the status the body set), or failed with the error.
    The error is re-raised: a failed run fails its job task too."""
    log.start(rec)
    try:
        yield rec
    except BaseException as e:
        rec.status = "failed"
        rec.error = f"{type(e).__name__}: {e}\n{''.join(traceback.format_exception(e))[-3000:]}"[:4000]
        rec.ended_at = clock()
        log.finish(rec)
        raise
    else:
        if rec.status == "running":
            rec.status = "succeeded"
        rec.ended_at = clock()
        log.finish(rec)


def config_rows(cfg: IngestConfig, config_hash: str, git_commit: str | None) -> list[dict]:
    """quote_ai_ops.ingestion_config rows: one per source and table, with the full source entry as JSON."""
    out = []
    for s in cfg.sources:
        entry = json.dumps(s.model_dump(mode="json", exclude_none=True), sort_keys=True)
        for t in s.tables:
            out.append({"source": s.name, "target_table": t.target, "type": s.type, "schedule": s.schedule,
                        "enabled": s.enabled, "keys": list(t.keys), "entry_json": entry,
                        "config_hash": config_hash, "git_commit": git_commit})
    return out


# ------------------------------------------------------------------ the ops tables ({ops} = catalog.schema)

DDL = {
    "ingestion_log": """
CREATE TABLE IF NOT EXISTS {ops}.ingestion_log (
  run_id STRING NOT NULL, source STRING NOT NULL, source_type STRING, target_tables ARRAY<STRING>,
  load_ids ARRAY<STRING>, status STRING NOT NULL, started_at TIMESTAMP, ended_at TIMESTAMP, error STRING,
  rows_read BIGINT, rows_inserted BIGINT, rows_updated BIGINT, rows_rejected BIGINT,
  rows_by_table MAP<STRING, BIGINT>, watermark_from STRING, watermark_to STRING, pipeline_update_id STRING,
  job_id STRING, job_run_id STRING, run_as STRING, bundle_target STRING, git_commit STRING, config_hash STRING)
COMMENT 'One row per ingestion run of a source: running, then succeeded, failed, partial or no_new_files.'""",
    "ingestion_file": """
CREATE TABLE IF NOT EXISTS {ops}.ingestion_file (
  load_id STRING NOT NULL, source STRING NOT NULL, kind STRING, path STRING NOT NULL, source_url STRING,
  release_date DATE, size_bytes BIGINT, md5 STRING, sha256 STRING NOT NULL, downloaded_at TIMESTAMP,
  row_count BIGINT, job_run_id STRING)
COMMENT 'Append-only manifest of every file landed in a landing volume, with its checksums.'""",
    "ingestion_config": """
CREATE TABLE IF NOT EXISTS {ops}.ingestion_config (
  source STRING NOT NULL, target_table STRING NOT NULL, type STRING, schedule STRING, enabled BOOLEAN,
  keys ARRAY<STRING>, entry_json STRING, config_hash STRING NOT NULL, git_commit STRING,
  valid_from TIMESTAMP, valid_to TIMESTAMP, is_current BOOLEAN)
COMMENT 'The ingestion config in force (config/quote_ai/ingest/sources.yml), per source and table, with history.'""",
    "v_ingestion_audit": """
CREATE OR REPLACE VIEW {ops}.v_ingestion_audit
COMMENT 'From one load ID: the run, who ran it from which code and config, and the files it landed.' AS
SELECT l.run_id, l.source, l.status, l.started_at, l.ended_at, l.error, l.rows_by_table, l.watermark_from,
       l.watermark_to, l.job_id, l.job_run_id, l.run_as, l.bundle_target, l.git_commit, l.config_hash,
       x.load_id, f.path, f.source_url, f.release_date, f.size_bytes, f.md5, f.sha256, f.downloaded_at,
       c.entry_json AS config_entry
FROM {ops}.ingestion_log l
LATERAL VIEW OUTER explode(l.load_ids) x AS load_id
LEFT JOIN {ops}.ingestion_file f ON f.load_id = x.load_id AND f.source = l.source
LEFT JOIN (SELECT DISTINCT source, config_hash, entry_json FROM {ops}.ingestion_config) c
  ON c.source = l.source AND c.config_hash = l.config_hash""",
}
