"""Job entry point for search and the eligibility engine (AI-15): one command per task of
resources/quote_ai/quote_ai_search_index.job.yml.

  setup      the wider range and its evidence, the catalog_skus and eligibility_evidence views, sku_search_doc
             (src/quote_ai/search/search_tables.sql)
  seed       the wider glove range G to R (quote_ai.mock.range): dbxlab_range and range_evidence (demo data,
             deterministic, overwritten)
  doc        refresh the search document by MERGE (src/quote_ai/pipelines/search_doc.sql)
  index      create the AI Search endpoint and the Delta Sync index if needed, then sync it (quote_ai.search.index)
             --what sku|knowledge   --endpoint NAME   --embedding-endpoint NAME
  functions  the quote_ai wheel into the packages volume, then check_eligibility and check_fulfilment
             (src/quote_ai/rules/sql/) --source-root PATH (the bundle's files) --packages /Volumes/...
  grants --app-sp ID   the app's service principal: SELECT on the index, EXECUTE on the two functions, SELECT on
             the inventory snapshot

Common: --catalog, --schemas logical=physical,... (from ${resources.schemas.<key>.name}).
Needs Spark (Databricks); the logic is plain Python, tested in tests/quote_ai/test_eligibility.py and test_search.py.
"""
import argparse
import inspect
import subprocess
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.storage import sql_files  # noqa: E402

WHEEL = "__QUOTE_AI_WHEEL__"


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "seed", "doc", "index", "functions", "grants"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--what", default="sku", choices=["sku", "knowledge"])
    p.add_argument("--endpoint", default="quote_ai_search")
    p.add_argument("--embedding-endpoint")
    p.add_argument("--source-root", help="the bundle's synced files (${workspace.file_path})")
    p.add_argument("--packages", help="/Volumes/<catalog>/<schema>/<volume> for the quote_ai wheel")
    p.add_argument("--app-sp")
    return p.parse_args(argv)


def run_sql_file(spark, path: Path, names: dict, replace: dict | None = None) -> None:
    for stmt in sql_files.load(path, **names):
        for k, v in (replace or {}).items():
            stmt = stmt.replace(k, v)
        spark.sql(stmt)


def seed(spark, names: dict) -> dict:
    from quote_ai.mock import range as mock_range
    products = {p["product_id"]: p for p in mock_range.products()}
    devices = mock_range.devices()
    skus = [s | {"product_name": products[s["product_id"]]["name"],
                 "device_description": devices[s["product_id"]][0]} for s in mock_range.skus()]
    evidence = [r.table_row() for r in mock_range.evidence()]
    for rows, table in ((skus, "dbxlab_range"), (evidence, "range_evidence")):
        fqn = f"{names['product']}.{table}"
        schema = spark.table(fqn).schema
        spark.createDataFrame([tuple(r.get(f.name) for f in schema.fields) for r in rows], schema=schema) \
            .write.mode("overwrite").insertInto(fqn)
    return {"range_skus": len(skus), "range_evidence": len(evidence)}


def publish_wheel(source_root: str, packages: str) -> str:
    """Build the quote_ai wheel from the bundle's files into the packages volume; returns its path. Verify before
    recording: pip on the job's serverless environment, and that a UC Python function can read the volume."""
    out = Path(packages) / "wheels"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "wheel", source_root, "--no-deps", "--wheel-dir", str(out)],
                   check=True)
    wheels = sorted(out.glob("quote_ai-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        raise SystemExit(f"no quote_ai wheel in {out}")
    return str(wheels[-1])


def app_grants(names: dict, app_sp: str) -> list[str]:
    """Least privilege: the app queries the index and calls the functions; it reads stock for fulfilment."""
    if not app_sp or not all(c.isalnum() or c == "-" for c in app_sp):
        raise SystemExit(f"not an application ID: {app_sp!r}")
    who = f"`{app_sp}`"
    return [f"GRANT USE SCHEMA ON SCHEMA {names['ai']} TO {who}",
            f"GRANT SELECT ON TABLE {names['ai']}.sku_search_index TO {who}",
            f"GRANT EXECUTE ON FUNCTION {names['ai']}.check_eligibility TO {who}",
            f"GRANT EXECUTE ON FUNCTION {names['ai']}.check_fulfilment TO {who}",
            f"GRANT EXECUTE ON FUNCTION {names['ai']}.eligibility_rules TO {who}",
            f"GRANT EXECUTE ON FUNCTION {names['ai']}.fulfilment_rules TO {who}",
            f"GRANT SELECT ON TABLE {names['sales']}.inventory_snapshot TO {who}"]


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "index":
        from quote_ai.llm import endpoints
        from quote_ai.search import index
        print("synced", index.sync(names["ai"], a.what, a.embedding_endpoint or endpoints.EMBED, a.endpoint))
        return
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    if a.command == "setup":
        run_sql_file(spark, SRC / "quote_ai" / "search" / "search_tables.sql", names)
    elif a.command == "seed":
        print(seed(spark, names))
    elif a.command == "doc":
        run_sql_file(spark, SRC / "quote_ai" / "pipelines" / "search_doc.sql", names)
    elif a.command == "functions":
        if not (a.source_root and a.packages):
            raise SystemExit("functions needs --source-root and --packages")
        wheel = publish_wheel(a.source_root, a.packages)
        for f in ("check_eligibility.sql", "check_fulfilment.sql"):
            run_sql_file(spark, SRC / "quote_ai" / "rules" / "sql" / f, names, {WHEEL: wheel})
        print("functions use", wheel)
    elif a.command == "grants":
        for stmt in app_grants(names, a.app_sp):
            spark.sql(stmt)


if __name__ == "__main__":
    main()
