"""Job entry point for the evaluation tables (AI-17): resources/quote_ai/quote_ai_eval_tables.job.yml.

  load   create the case tables in the eval schema (evaluation/quote_ai/eval_tables.sql) and overwrite them with the
         cases of one suite version (evaluation/quote_ai/<version>/{search,eligibility,comparison}/cases.jsonl)

  --catalog, --schemas quote_ai_eval=<physical name>, --version v1
The cases are plain JSON lines; rows_for (plain Python) is tested in tests/quote_ai/test_scorers.py.
"""
import argparse
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
ROOT = SRC.parent
sys.path.insert(0, str(SRC))
from quote_ai.storage import sql_files  # noqa: E402

COLUMNS = {
    "search": ("case_id", "suite_version", "need", "account_id", "case_json", "loaded_at"),
    "eligibility": ("case_id", "suite_version", "glove", "evidence_snapshot", "status", "case_json", "loaded_at"),
    "comparison": ("case_id", "suite_version", "question", "rep", "case_json", "loaded_at"),
}


def rows_for(suite: str, version: str = "v1", at: datetime | None = None) -> list[tuple]:
    at = at or datetime.now(timezone.utc)
    path = ROOT / "evaluation" / "quote_ai" / version / suite / "cases.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        values = {"suite_version": version, "case_json": json.dumps(c), "loaded_at": at} | c
        out.append(tuple(values.get(col) for col in COLUMNS[suite]))
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["load"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--version", default="v1")
    a = p.parse_args(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(x.split("=", 1) for x in a.schemas.split(",") if x))
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    for stmt in sql_files.load(ROOT / "evaluation" / "quote_ai" / "eval_tables.sql", **names):
        spark.sql(stmt)
    for suite in COLUMNS:
        fqn = f"{names['eval']}.{suite}_cases"
        rows = rows_for(suite, a.version)
        spark.createDataFrame(rows, spark.table(fqn).schema).write.mode("overwrite").insertInto(fqn)
        print(suite, len(rows), "cases ->", fqn)


if __name__ == "__main__":
    main()
