"""Job entry point for supplier documents (AI-09): one command per task of resources/quote_ai/
quote_ai_mock_documents.job.yml, plus the QA reviewer's sign-off.

  setup      document_registry, document_qa_signoff, supplier_price_list (src/quote_ai/product/documents.sql)
  generate   the synthetic documents (quote_ai.mock.documents) as PDFs into the documents volume, with its README, and
             each one registered as received (document_registry; is_current recomputed per document ID)
             [--scenarios glove_a_report,other_catalog_report,spec_v4,glove_b_report]  the demo's drops
             [--demo-signoff]   spec sheets and the reports already on file (Gloves C, D) signed off by demo:qa
                                (demo data only); Glove A's report is signed off by a person, with signoff
  signoff --document-id ID --version N --reviewer NAME   a QA reviewer signs one document version off
  views      glove_evidence and product_documents (src/quote_ai/product/evidence_views.sql), after both pipelines

Common: --catalog, --schemas logical=physical,... (from ${resources.schemas.<key>.name}), --volume catalog.schema.vol.
Needs Spark (Databricks); the logic is plain Python, tested in tests/quote_ai/test_document_evidence.py.
"""
import argparse
import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.mock import documents as mock_docs  # noqa: E402
from quote_ai.product import evidence  # noqa: E402
from quote_ai.storage import sql_files  # noqa: E402

DEMO_QA = "demo:qa"


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "generate", "signoff", "views"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--volume", help="catalog.schema.volume of quote_ai_documents")
    p.add_argument("--scenarios", default="")
    p.add_argument("--demo-signoff", action="store_true")
    p.add_argument("--document-id")
    p.add_argument("--version", type=int)
    p.add_argument("--reviewer")
    return p.parse_args(argv)


def volume_root(volume: str) -> str:
    catalog, schema, name = volume.split(".")
    return f"/Volumes/{catalog}/{schema}/{name}"


def run_sql_file(spark, path: Path, names: dict) -> None:
    for stmt in sql_files.load(path, **names):
        spark.sql(stmt)


def register(spark, names: dict, rows: list[dict]) -> None:
    """MERGE the received documents by (document_id, version), then recompute is_current for every document."""
    fqn = f"{names['product']}.document_registry"
    existing = [r.asDict() for r in spark.table(fqn).collect()]
    by_key = {(r["document_id"], int(r["version"])): r for r in existing}
    now = datetime.now(timezone.utc)
    for r in rows:
        by_key[(r["document_id"], int(r["version"]))] = r | {"registered_at": by_key.get(
            (r["document_id"], int(r["version"])), {}).get("registered_at") or now}
    out = evidence.current_versions(by_key.values())
    schema = spark.table(fqn).schema
    df = spark.createDataFrame([tuple(r.get(f.name) for f in schema.fields) for r in out], schema=schema)
    df.write.mode("overwrite").insertInto(fqn)       # small table; rewriting it keeps is_current consistent


def generate(spark, names: dict, volume: str, scenarios: list[str], demo_signoff: bool) -> dict:
    skus = [r.asDict() for r in spark.sql(
        f"SELECT sku_id, product_id, size, supplier_catalog_number FROM {names['product']}.dbxlab_sku").collect()]
    docs = mock_docs.documents(skus or None, scenarios)
    root = volume_root(volume)
    Path(root, "README.md").write_text(mock_docs.README, encoding="utf-8")
    rows = []
    for d in docs:
        data = mock_docs.pdf_bytes(d.lines())
        target = Path(root, d.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():                  # received once; a changed document is a new version, never an edit
            target.write_bytes(data)
        if d.document_type != "request":
            rows.append(mock_docs.registry_row(d, root, data))
    register(spark, names, rows)
    if demo_signoff:
        for d in docs:
            if mock_docs.demo_signed(d):
                signoff(spark, names, d.document_id, d.version, DEMO_QA)
    return {"documents": len(docs), "registered": len(rows), "scenarios": scenarios}


def signoff(spark, names: dict, document_id: str, version: int, reviewer: str) -> None:
    """A QA reviewer signs one document version off (check 5). Idempotent per version."""
    spark.sql(f"""MERGE INTO {names['product']}.document_qa_signoff t
                  USING (SELECT :id AS document_id, :v AS document_version, :by AS verified_by,
                                current_timestamp() AS verified_at) s
                  ON t.document_id = s.document_id AND t.document_version = s.document_version
                  WHEN NOT MATCHED THEN INSERT *""",
              args={"id": document_id, "v": version, "by": reviewer})


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "setup":
        run_sql_file(spark, SRC / "quote_ai" / "product" / "documents.sql", names)
    elif a.command == "generate":
        if not a.volume:
            raise SystemExit("generate needs --volume")
        print(generate(spark, names, a.volume, [s for s in a.scenarios.split(",") if s], a.demo_signoff))
    elif a.command == "signoff":
        if not (a.document_id and a.version and a.reviewer):
            raise SystemExit("signoff needs --document-id, --version and --reviewer")
        signoff(spark, names, a.document_id, a.version, a.reviewer)
    elif a.command == "views":
        run_sql_file(spark, SRC / "quote_ai" / "product" / "evidence_views.sql", names)


if __name__ == "__main__":
    main()
