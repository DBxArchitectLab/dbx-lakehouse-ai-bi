"""The Spark side of the engine, on Databricks only: the ops tables, MERGE into bronze, the API source.

Kept thin on purpose: the decisions are in engine.py, download.py and log.py, tested without Spark.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from quote_ai.ingest import api_source
from quote_ai.ingest.config import IngestConfig
from quote_ai.ingest.lineage import merge_sql, qualified
from quote_ai.ingest.log import DDL, FileRecord, RunRecord, config_rows, utcnow

_RUN_COLS = list(RunRecord.model_fields)
_FILE_COLS = list(FileRecord.model_fields)


def _lit(v: Any) -> str:
    """A SQL literal for the log's own values (never user input)."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, datetime):
        return f"TIMESTAMP'{v.isoformat()}'"
    if isinstance(v, date):
        return f"DATE'{v.isoformat()}'"
    if isinstance(v, list):
        return "array(" + ", ".join(_lit(x) for x in v) + ")" if v else "CAST(array() AS ARRAY<STRING>)"
    if isinstance(v, dict):
        if not v:
            return "CAST(map() AS MAP<STRING, BIGINT>)"
        return "map(" + ", ".join(f"{_lit(k)}, {_lit(x)}" for k, x in v.items()) + ")"
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


class SparkIO:
    def __init__(self, spark) -> None:
        self.spark = spark
        api_source.register(spark)

    def table_exists(self, fqn: str) -> bool:
        return self.spark.catalog.tableExists(fqn)

    def query(self, sql: str) -> list[tuple]:
        return [tuple(r) for r in self.spark.sql(sql).collect()]

    def execute(self, sql: str) -> None:
        self.spark.sql(sql)

    def read_api(self, options: dict[str, str]):
        return self.spark.read.format("quote_ai_gudid_api").options(**options).load()

    def merge(self, df, target_fqn: str, keys: list[str], lineage: dict[str, Any], sequence_by: str) -> dict:
        from pyspark.sql import functions as F
        staged = (df.withColumn("_source", F.lit(lineage["_source"]))
                    .withColumn("_file_or_request", F.col("request"))
                    .withColumn("_load_id", F.lit(lineage["_load_id"]))
                    .withColumn("_ingested_at", F.current_timestamp())
                    .withColumn("_release_date", F.lit(lineage["_release_date"]).cast("date"))
                    .drop("request"))
        # Materialize once: the API is called exactly once per record (serverless has no DataFrame cache), and the
        # MERGE and the counts read the staged rows.
        staging = f"{target_fqn}_staging"
        staged.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(staging)
        try:
            read = int(self.spark.sql(f"SELECT count(*) FROM {qualified(staging)}").first()[0])
            if not self.table_exists(target_fqn):
                self.spark.sql(f"CREATE TABLE {qualified(target_fqn)} AS SELECT * FROM {qualified(staging)} "
                               f"WHERE false")
            m = self.spark.sql(merge_sql(target_fqn, staging, keys, sequence_by)).collect()
        finally:
            self.spark.sql(f"DROP TABLE IF EXISTS {qualified(staging)}")
        row = m[0].asDict() if m else {}
        return {"read": read, "inserted": row.get("num_inserted_rows"), "updated": row.get("num_updated_rows")}

    def count_by_load_ids(self, fqn: str, load_ids: list[str]) -> int:
        if not load_ids or not self.table_exists(fqn):
            return 0
        ids = ", ".join(_lit(i) for i in load_ids)
        return int(self.spark.sql(f"SELECT count(*) FROM {qualified(fqn)} WHERE `_load_id` IN ({ids})").first()[0])


class DeltaLog:
    """LogWriter on the ops tables. Rows are written by SQL with literals from our own records."""

    def __init__(self, io: SparkIO, ops_fqn: str) -> None:
        self.io, self.ops = io, qualified(ops_fqn)

    def ensure_tables(self) -> None:
        for ddl in DDL.values():
            self.io.execute(ddl.format(ops=self.ops))

    def _upsert_run(self, rec: RunRecord) -> None:
        d = rec.model_dump()
        values = ", ".join(f"{_lit(d[c])} AS `{c}`" for c in _RUN_COLS)
        self.io.execute(f"MERGE INTO {self.ops}.ingestion_log t USING (SELECT {values}) s ON t.run_id = s.run_id "
                        f"WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")

    start = _upsert_run
    finish = _upsert_run

    def get(self, run_id: str) -> RunRecord | None:
        rows = self.io.spark.sql(f"SELECT * FROM {self.ops}.ingestion_log WHERE run_id = {_lit(run_id)}").collect()
        return RunRecord.model_validate(rows[0].asDict(recursive=True)) if rows else None

    def add_files(self, recs: list[FileRecord]) -> int:
        n = 0
        for r in recs:
            d = r.model_dump()
            values = ", ".join(f"{_lit(d[c])} AS `{c}`" for c in _FILE_COLS)
            out = self.io.spark.sql(
                f"MERGE INTO {self.ops}.ingestion_file t USING (SELECT {values}) s "
                f"ON t.source = s.source AND t.path = s.path AND t.sha256 = s.sha256 "
                f"WHEN NOT MATCHED THEN INSERT *").collect()
            n += int(out[0].asDict().get("num_inserted_rows", 0)) if out else 0
        return n

    def files(self, source: str, job_run_id: str | None = None) -> list[FileRecord]:
        where = f"source = {_lit(source)}" + (f" AND job_run_id = {_lit(job_run_id)}" if job_run_id else "")
        rows = self.io.spark.sql(f"SELECT * FROM {self.ops}.ingestion_file WHERE {where}").collect()
        return [FileRecord.model_validate(r.asDict()) for r in rows]

    def last_success(self, source: str) -> RunRecord | None:
        rows = self.io.spark.sql(
            f"SELECT * FROM {self.ops}.ingestion_log WHERE source = {_lit(source)} "
            f"AND status IN ('succeeded', 'partial') ORDER BY coalesce(ended_at, started_at) DESC LIMIT 1").collect()
        return RunRecord.model_validate(rows[0].asDict(recursive=True)) if rows else None

    def sync_config(self, cfg: IngestConfig, config_hash: str, git_commit: str | None) -> bool:
        """Record the config in force (SCD type 2). Returns True when it changed."""
        current = self.io.query(f"SELECT DISTINCT config_hash FROM {self.ops}.ingestion_config WHERE is_current")
        if [r[0] for r in current] == [config_hash]:
            return False
        now = utcnow()
        self.io.execute(f"UPDATE {self.ops}.ingestion_config SET valid_to = {_lit(now)}, is_current = false "
                        f"WHERE is_current")
        rows = config_rows(cfg, config_hash, git_commit)
        selects = " UNION ALL ".join(
            "SELECT " + ", ".join(f"{_lit(r[k])} AS `{k}`" for k in r)
            + f", {_lit(now)} AS valid_from, CAST(NULL AS TIMESTAMP) AS valid_to, true AS is_current"
            for r in rows)
        self.io.execute(f"INSERT INTO {self.ops}.ingestion_config BY NAME {selects}")
        return True


def run_as(spark) -> str:
    return spark.sql("SELECT current_user()").first()[0]
