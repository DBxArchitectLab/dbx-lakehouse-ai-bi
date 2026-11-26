"""Job entry point for Lakebase (AI-20): one command per task of resources/quote_ai/quote_ai_lakebase.job.yml.

  schema     apply src/quote_ai/rules/sql/lakebase_schema.sql to the quote_ai database (idempotent)
  migrate    the one-off move from Delta (quote_ai.ops.migrate_quotes_to_lakebase): copy, prove counts and checksums
             per table, or stop and roll back. Run only with --writes-paused, after the app was redeployed with
             QUOTE_AI_READ_ONLY=1; prints the proof. The old Delta tables are renamed *_pre_ai20 (kept a release).
  sync       the analytics copy: Lakebase -> {sales}.quote, quote_line, quote_event, quote_approval
             (src/quote_ai/storage/lakebase_sync.sql), through the database catalog --lakebase-catalog
  purge      retention: conversation messages older than 30 days

Common: --catalog, --schemas logical=physical,... ; --instance (quote-ai-db), --database (quote_ai).
Needs Databricks (Spark for Delta, psycopg for Lakebase); the logic is plain Python, tested on SQLite
(tests/quote_ai/test_lakebase_repository.py, test_memory.py).
"""
import argparse
import inspect
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.storage import sql_files  # noqa: E402

PRE = "_pre_ai20"


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["schema", "migrate", "sync", "purge"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--instance", default="quote-ai-db")
    p.add_argument("--database", default="quote_ai")
    p.add_argument("--lakebase-catalog", help="the Lakebase database registered in Unity Catalog")
    p.add_argument("--writes-paused", action="store_true", help="migrate: the app is read-only (QUOTE_AI_READ_ONLY=1)")
    return p.parse_args(argv)


def sync_statements(names: dict, lakebase_catalog: str) -> list[str]:
    if not sql_files._IDENT.match(lakebase_catalog):
        raise SystemExit(f"not a catalog name: {lakebase_catalog!r}")
    return [s.replace("__LAKEBASE__", f"{lakebase_catalog}.public")
            for s in sql_files.load(SRC / "quote_ai" / "storage" / "lakebase_sync.sql", **names)]


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    from quote_ai.storage.lakebase import apply_schema, lakebase_db
    if a.command == "schema":
        print("statements", apply_schema(lakebase_db(a.instance, a.database)))
        return
    if a.command == "purge":
        from quote_ai.agents.memory import Memory
        print("purged", Memory(lakebase_db(a.instance, a.database)).purge())
        return
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    if a.command == "sync":
        if not a.lakebase_catalog:
            raise SystemExit("sync needs --lakebase-catalog")
        if not spark.catalog.tableExists(f"{names['sales']}.quote{PRE}"):
            # Before the migration Delta is still the truth: replacing it from an empty Lakebase would erase it
            print("sync skipped: the migration has not run (no quote_pre_ai20); Delta is still the source of truth")
            return
        for stmt in sync_statements(names, a.lakebase_catalog):
            spark.sql(stmt)
        return
    if not a.writes_paused:
        raise SystemExit("migrate: redeploy the app with QUOTE_AI_READ_ONLY=1 first, then pass --writes-paused")
    from quote_ai.ops import migrate_quotes_to_lakebase as m
    db = lakebase_db(a.instance, a.database)

    def read_delta(table: str) -> list[dict]:
        return [r.asDict() for r in spark.table(f"{names['sales']}.{table}").collect()]

    def rename_old() -> None:
        for t in m.TABLES:
            spark.sql(f"ALTER TABLE {names['sales']}.{t} RENAME TO {names['sales']}.{t}{PRE}")

    report = m.migrate(read_delta, db, pause=lambda: None,
                       switch=lambda: print("switch: redeploy the app with QUOTE_AI_STORE=lakebase"),
                       sync=rename_old, resume=lambda: print("resume: unset QUOTE_AI_READ_ONLY and redeploy"))
    print(m.as_json(report))


if __name__ == "__main__":
    main()
