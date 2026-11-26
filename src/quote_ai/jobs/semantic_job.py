"""Job entry point for the semantic layer (AI-10, AI-11): one command per task of resources/quote_ai/
quote_ai_semantic.job.yml.

  setup     quote_fact and order_line (src/quote_ai/semantic/sales_tables.sql)
  seed      DBxLab's synthetic sales history (quote_ai.mock.sales): deterministic, so a rerun writes the same rows
  metrics   the metric views quote_metrics and order_metrics (src/quote_ai/semantic/*_metrics.sql): change a
            definition in the file, redeploy, rerun, and every consumer follows
  comments  table and column comments for everything the Genie Agent reads (src/quote_ai/semantic/comments.sql, AI-11)

Common: --catalog and --schemas logical=physical,... (from ${resources.schemas.<key>.name}).
Needs Spark (Databricks); the definitions are tested in tests/quote_ai/test_metric_definitions.py.
"""
import argparse
import inspect
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.mock import sales  # noqa: E402
from quote_ai.storage import sql_files  # noqa: E402

SEMANTIC = SRC / "quote_ai" / "semantic"


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "seed", "metrics", "comments"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    return p.parse_args(argv)


def run_sql_file(spark, path: Path, names: dict) -> None:
    for stmt in sql_files.load(path, **names):
        spark.sql(stmt)


def _write(spark, rows: list[dict], fqn: str) -> None:
    """Overwrite a demo table with generated rows, in the table's own column order and types."""
    from decimal import Decimal

    from pyspark.sql.types import DecimalType
    schema = spark.table(fqn).schema

    def value(f, v):
        return Decimal(str(v)) if isinstance(f.dataType, DecimalType) and isinstance(v, float) else v
    df = spark.createDataFrame([tuple(value(f, r.get(f.name)) for f in schema.fields) for r in rows], schema=schema)
    df.write.mode("overwrite").insertInto(fqn)


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "setup":
        run_sql_file(spark, SEMANTIC / "sales_tables.sql", names)
    elif a.command == "seed":
        facts = sales.quote_facts()
        _write(spark, facts, f"{names['sales']}.quote_fact")
        _write(spark, sales.order_lines(facts), f"{names['sales']}.order_line")
    elif a.command == "metrics":
        run_sql_file(spark, SEMANTIC / "quote_metrics.sql", names)
        run_sql_file(spark, SEMANTIC / "order_metrics.sql", names)
    elif a.command == "comments":
        run_sql_file(spark, SEMANTIC / "comments.sql", names)


if __name__ == "__main__":
    main()
