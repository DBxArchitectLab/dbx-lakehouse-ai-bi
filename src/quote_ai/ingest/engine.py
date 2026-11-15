"""The engine: one way to run any source in the config.

A daily job runs, in order:
  1. land     file sources: download (or generate) new release files into the landing volume; the run is logged as
              running, with the load IDs it landed
  2. files    the bronze pipeline (bronze_pipeline.py): Auto Loader picks up the new files, keyed by the config
  3. record   each file source's run finished: succeeded with its row counts, or failed with the pipeline's state
  4. api      API sources: look up only the records that changed since the last successful run, MERGE on keys
  5. checks   the Ingestion suite on bronze (checks.py)

Spark is reached only through SparkIO (spark_io.py), so the run logic is tested here without a cluster.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from quote_ai.ingest.config import IngestConfig, Source
from quote_ai.ingest.download import land_source
from quote_ai.ingest.http import Http
from quote_ai.ingest.lineage import load_id_for, qualified, release_tag
from quote_ai.ingest.log import LogWriter, RunContext, logged_run, new_run, utcnow

SUCCESS_STATES = {"SUCCESS", "SUCCEEDED", "SUCCESS_WITH_FAILURES"}
# The API target's column for each lookup parameter: what we already hold, so we don't ask again.
TARGET_COLUMN = {"di": "primary_di", "record_key": "public_device_record_key"}


class Names(BaseModel):
    """Logical names in the config (quote_ai_gudid_bronze.device_raw) to physical names in this environment: the
    catalog of
    the bundle target, and the schema names it deployed (prefixed per developer in dev)."""
    catalog: str
    schemas: dict[str, str] = {}

    def schema(self, logical: str) -> str:
        return self.schemas.get(logical, logical)

    def table(self, target: str) -> str:
        schema, table = target.split(".")
        return f"{self.catalog}.{self.schema(schema)}.{table}"

    def volume_path(self, volume: str) -> Path:
        schema, vol = volume.split(".")
        return Path(f"/Volumes/{self.catalog}/{self.schema(schema)}/{vol}")


class SparkIO(Protocol):
    def table_exists(self, fqn: str) -> bool: ...
    def query(self, sql: str) -> list[tuple]: ...
    def read_api(self, options: dict[str, str]) -> Any: ...
    def merge(self, df: Any, target_fqn: str, keys: list[str], lineage: dict[str, Any], sequence_by: str) -> dict: ...
    def count_by_load_ids(self, fqn: str, load_ids: list[str]) -> int: ...


def run_id_for(ctx: RunContext, source: str) -> str:
    return f"{ctx.job_run_id or 'local-' + utcnow().strftime('%Y%m%dT%H%M%S')}:{source}"


# ------------------------------------------------------------------ 1. land

def land(cfg: IngestConfig, schedule: str, names: Names, log: LogWriter, ctx: RunContext, today: date,
         release_date: date | None = None, http_factory: Callable[[], Http] = Http,
         generators: dict[str, Callable] | None = None, only: str | None = None) -> dict[str, list[str]]:
    """Land every enabled file source of this schedule. Each gets a run row: running with its load IDs (finished by
    record), no_new_files, or failed. Returns the load IDs landed per source."""
    landed: dict[str, list[str]] = {}
    for s in cfg.enabled(type="file", schedule=schedule):
        if only and s.name != only:
            continue
        rec = new_run(run_id_for(ctx, s.name), s.name, s.type, [t.target for t in s.tables], ctx)
        log.start(rec)
        try:
            files = land_source(http_factory(), s, names.volume_path(s.volume), log, today, release_date,
                                ctx.job_run_id, generators)
        except Exception as e:
            rec.status, rec.error, rec.ended_at = "failed", f"{type(e).__name__}: {e}"[:4000], utcnow()
            log.finish(rec)
            raise
        rec.load_ids = sorted({f.load_id for f in files})
        if not rec.load_ids:
            rec.status, rec.ended_at = "no_new_files", utcnow()
        log.finish(rec)
        landed[s.name] = rec.load_ids
    return landed


# ------------------------------------------------------------------ 3. record

def record(cfg: IngestConfig, schedule: str, names: Names, log: LogWriter, io: SparkIO, ctx: RunContext,
           pipeline_state: str, pipeline_update_id: str | None = None) -> list[str]:
    """Finish the file sources' runs after the pipeline: row counts per table for the load IDs they landed.
    Returns the sources that failed."""
    failed = []
    for s in cfg.enabled(type="file", schedule=schedule):
        rec = log.get(run_id_for(ctx, s.name))
        if rec is None or rec.status != "running":
            continue
        rec.pipeline_update_id = pipeline_update_id
        if pipeline_state.upper() in SUCCESS_STATES:
            rec.rows_by_table = {t.target: io.count_by_load_ids(names.table(t.target), rec.load_ids)
                                 for t in s.tables}
            rec.rows_read = sum(rec.rows_by_table.values())
            rec.status = "succeeded"
        else:
            rec.status, rec.error = "failed", f"bronze pipeline update ended {pipeline_state}"
            failed.append(s.name)
        rec.ended_at = utcnow()
        log.finish(rec)
    return failed


# ------------------------------------------------------------------ 4. api

def changed_ids_sql(source_fqn: str, column: str, target_fqn: str | None, target_column: str,
                    since: str | None) -> str:
    """The identifiers to look up: changed since the watermark, and not already held at that release or later (the
    cache, which also lets a capped run continue where it stopped)."""
    col = qualified(column)
    sql = [f"SELECT s.{col} AS id, max(s.`_release_date`) AS released FROM {qualified(source_fqn)} s"]
    if target_fqn:
        sql.append(f"LEFT ANTI JOIN {qualified(target_fqn)} t ON t.{qualified(target_column)} = s.{col} "
                   f"AND t.`_release_date` >= s.`_release_date`")
    where = [f"s.{col} IS NOT NULL"]
    if since:
        date.fromisoformat(since)          # a date, never free text, goes into the SQL
        where.append(f"s.`_release_date` > DATE'{since}'")
    sql.append("WHERE " + " AND ".join(where))
    sql.append(f"GROUP BY s.{col} ORDER BY released, id")
    return "\n".join(sql)


def run_api(cfg: IngestConfig, name: str, names: Names, log: LogWriter, io: SparkIO, ctx: RunContext,
            today: date) -> Any:
    s: Source = cfg.source(name)
    t = s.tables[0]
    target = names.table(t.target)
    rec = new_run(run_id_for(ctx, s.name), s.name, s.type, [t.target], ctx)
    with logged_run(log, rec) as rec:
        last = log.last_success(s.name)
        since = last.watermark_to if (last and s.ids_from.changed_since == "watermark") else None
        rec.watermark_from = since
        rows = io.query(changed_ids_sql(names.table(s.ids_from.table), s.ids_from.column,
                                        target if io.table_exists(target) else None,
                                        TARGET_COLUMN[s.ids_from.param], since))
        if not rows:
            rec.status, rec.watermark_to, rec.rows_read = "no_new_files", since, 0
            return rec
        cap = s.api.max_requests_per_run
        batch = rows[:cap]
        released = max(r[1] for r in batch)
        released = released if isinstance(released, date) else date.fromisoformat(str(released))
        load_id = load_id_for(s.name, release_tag(released))
        rec.load_ids = [load_id]
        df = io.read_api({"base_url": s.base_url, "endpoint": s.endpoint, "param": s.ids_from.param,
                          "ids": ",".join(str(r[0]) for r in batch), "limits": json.dumps(s.api.model_dump())})
        counts = io.merge(df, target, t.keys, {"_source": s.name, "_load_id": load_id, "_release_date": released},
                          t.sequence_by)
        rec.rows_read = counts.get("read")
        rec.rows_inserted = counts.get("inserted")
        rec.rows_updated = counts.get("updated")
        rec.rows_by_table = {t.target: counts.get("read") or 0}
        if len(rows) > cap:
            # Capped: the watermark stays, and the cache skips what this run fetched, so the next run continues.
            rec.status, rec.watermark_to = "partial", since
            rec.error = f"capped at max_requests_per_run={cap}: {len(rows) - cap} records left for the next run"
        else:
            rec.watermark_to = released.isoformat()
    return rec
