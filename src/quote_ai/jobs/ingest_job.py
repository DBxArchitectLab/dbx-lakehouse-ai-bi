"""Job entry point for QuoteAI ingestion: one command per task of the generated jobs
(resources/quote_ai/quote_ai_ingest_<schedule>.job.yml).

  setup                         the ops tables and audit view; record the config in force
                                (quote_ai_ops.ingestion_config)
  land --schedule S             land the new release files of the file sources on that schedule
       [--release-date D]       for a full release (job quote_ai_ingest_once, parameter release_date, yyyy-mm-dd)
  record --schedule S --pipeline-state STATE
                                finish the file sources' runs after the bronze pipeline
  api --source NAME             an API source: look up what changed, MERGE on keys
  checks                        the Ingestion suite on bronze; fails the task if any check fails

Common: --config, --catalog, --schemas logical=physical,..., and the run's job ID, run ID, bundle target and commit.
Identity: the job's run_as (sp-dbx-lakehouse in dev-shared and prod); nobody else writes to bronze or ops.
"""
import argparse
import inspect
import sys
from datetime import date
from pathlib import Path

# src/ on the path, so quote_ai imports. Not from __file__: a spark_python_task on serverless runs this file through
# exec(), which doesn't define it; the code object still carries the file's path.
sys.path.insert(0, str(Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]))
from quote_ai.ingest import engine  # noqa: E402
from quote_ai.ingest.checks import bronze_checks  # noqa: E402
from quote_ai.ingest.config import OPS_SCHEMA, config_hash, load_config  # noqa: E402
from quote_ai.ingest.log import RunContext  # noqa: E402
from quote_ai.ingest.synthetic import GENERATORS  # noqa: E402


def _blank(v):
    """Unresolved or empty job values ("", "{{...}}") count as not given."""
    return None if not v or v.startswith("{{") or v.startswith("${") else v


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "land", "record", "api", "checks"])
    p.add_argument("--config", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--schedule")
    p.add_argument("--release-date")
    p.add_argument("--pipeline-state")
    p.add_argument("--source")
    p.add_argument("--job-id")
    p.add_argument("--job-run-id")
    p.add_argument("--bundle-target")
    p.add_argument("--git-commit")
    return p.parse_args(argv)


def main(argv=None) -> int:
    a = parse(argv if argv is not None else sys.argv[1:])
    from pyspark.sql import SparkSession

    from quote_ai.ingest.spark_io import DeltaLog, SparkIO, run_as

    spark = SparkSession.builder.getOrCreate()
    cfg = load_config(a.config)
    names = engine.Names(catalog=a.catalog,
                         schemas=dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    ops = f"{names.catalog}.{names.schema(OPS_SCHEMA)}"
    io = SparkIO(spark)
    log = DeltaLog(io, ops)
    h = config_hash(cfg)
    ctx = RunContext(job_id=_blank(a.job_id), job_run_id=_blank(a.job_run_id), run_as=run_as(spark),
                     bundle_target=_blank(a.bundle_target), git_commit=_blank(a.git_commit), config_hash=h)
    today = date.today()

    if a.command == "setup":
        log.ensure_tables()
        changed = log.sync_config(cfg, h, ctx.git_commit)
        print(f"ops tables ready in {ops}; config {h} {'recorded' if changed else 'unchanged'}")
    elif a.command == "land":
        rd = _blank(a.release_date)
        landed = engine.land(cfg, a.schedule, names, log, ctx, today,
                             release_date=date.fromisoformat(rd) if rd else None, generators=GENERATORS)
        for source, ids in landed.items():
            print(f"{source}: {len(ids)} load(s) landed {ids}")
    elif a.command == "record":
        failed = engine.record(cfg, a.schedule, names, log, io, ctx, a.pipeline_state or "UNKNOWN")
        if failed:
            print(f"failed: {failed}")
            return 1
    elif a.command == "api":
        rec = engine.run_api(cfg, a.source, names, log, io, ctx, today)
        print(f"{rec.source}: {rec.status}, {rec.rows_read} rows, watermark {rec.watermark_from} -> {rec.watermark_to}")
    elif a.command == "checks":
        failures = 0
        for c in bronze_checks(cfg, names.table, ops):
            if not io.table_exists(names.table(c.table)):
                print(f"SKIP {c.table} {c.name}: table not created yet")
                continue
            n = io.query(c.sql)[0][0]
            failures += n > 0
            print(f"{'PASS' if n == 0 else 'FAIL'} {c.table} {c.name}: {n}")
        if failures:
            return 1
    return 0


if __name__ == "__main__":
    code = main()
    if code:
        raise SystemExit(code)
