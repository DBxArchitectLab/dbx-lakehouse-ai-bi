"""Job entry point for the knowledge index (AI-16, AI-17): one command per task of
resources/quote_ai/quote_ai_knowledge_index.job.yml.

  setup     knowledge_chunks (CDF on) and rep_account (src/quote_ai/search/knowledge_tables.sql)
  accounts  rep_account from quote_ai.mock.knowledge.REP_ACCOUNTS (demo data)
  chunk     every knowledge source, chunked (section-aware and fixed500), MERGEd into knowledge_chunks:
            the supplier documents as ai_parse_document read them (parsed_documents, current versions from
            document_registry; business truth when glove_evidence has verified rows for that version), the GUDID
            descriptions of the DBxLab SKUs and of the wider range, the playbook, GMDN notes and price sheets
  index     the AI Search index knowledge_index (quote_ai.search.index), synced
  grants --app-sp ID   SELECT on knowledge_index and rep_account for the app

Common: --catalog, --schemas logical=physical,... The logic is plain Python (quote_ai.search.chunks,
quote_ai.mock.knowledge.sources), tested in tests/quote_ai/test_compare.py.
"""
import argparse
import inspect
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.storage import sql_files  # noqa: E402

COLUMNS = ["chunk_id", "doc_id", "doc_version", "product_id", "sku_ids", "account_id", "section", "truth_layer",
           "chunking", "text"]


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "accounts", "chunk", "index", "grants"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--endpoint", default="quote_ai_search")
    p.add_argument("--embedding-endpoint")
    p.add_argument("--app-sp")
    return p.parse_args(argv)


def chunk(spark, names: dict) -> dict:
    from quote_ai.mock import knowledge
    from quote_ai.product import evidence
    from quote_ai.search.chunks import chunk_document
    p = names["product"]
    docs = spark.sql(f"""
        SELECT r.document_id, r.version, r.product_id, {evidence.PARSED_TEXT_SQL} AS text
        FROM {p}.parsed_documents d JOIN {p}.document_registry r ON r.path = d.path
        WHERE r.is_current AND r.document_type IN ('spec_sheet', 'test_report', 'marketing')""").collect()
    verified = {(r.document_id, int(r.document_version)) for r in spark.sql(
        f"SELECT DISTINCT document_id, document_version FROM {p}.glove_evidence "
        f"WHERE verification_status = 'verified' AND document_id IS NOT NULL").collect()}
    descriptions = {r.product_id: r.device_description for r in spark.sql(f"""
        SELECT s.product_id, max(d.device_description) AS device_description
        FROM {p}.dbxlab_sku s JOIN {names['gudid_gold']}.glove_devices d ON d.primary_di = s.supplier_di
        GROUP BY s.product_id
        UNION ALL
        SELECT product_id, max(device_description) FROM {p}.dbxlab_range GROUP BY product_id""").collect()}
    skus: dict[str, list[str]] = {}
    for r in spark.sql(f"SELECT product_id, sku_id FROM {p}.catalog_skus").collect():
        skus.setdefault(r.product_id, []).append(r.sku_id)
    rows = []
    for doc_id, version, text, meta in knowledge.sources(
            [(d.document_id, d.version, d.text or "", d.product_id) for d in docs], descriptions, skus, verified):
        rows += chunk_document(doc_id, version, text, **meta)
    from pyspark.sql import types as T
    schema = T.StructType([T.StructField(c, T.ArrayType(T.StringType()) if c == "sku_ids" else
                                         T.IntegerType() if c == "doc_version" else T.StringType()) for c in COLUMNS])
    spark.createDataFrame([tuple(r[c] for c in COLUMNS) for r in rows], schema).createOrReplaceTempView("new_chunks")
    spark.sql(f"""MERGE INTO {names['ai']}.knowledge_chunks t USING new_chunks s ON t.chunk_id = s.chunk_id
                  WHEN MATCHED AND (t.text <> s.text OR t.truth_layer <> s.truth_layer OR t.account_id <> s.account_id)
                    THEN UPDATE SET *
                  WHEN NOT MATCHED THEN INSERT *
                  WHEN NOT MATCHED BY SOURCE THEN DELETE""")
    return {"chunks": len(rows), "documents": len(docs), "verified_document_versions": len(verified)}


def accounts(spark, names: dict) -> int:
    from quote_ai.mock import knowledge
    rows = [(rep, acc) for rep, accs in knowledge.REP_ACCOUNTS.items() for acc in accs]
    spark.createDataFrame(rows, "rep_id STRING, account_id STRING").write.mode("overwrite") \
        .insertInto(f"{names['sales']}.rep_account")
    return len(rows)


def app_grants(names: dict, app_sp: str) -> list[str]:
    if not app_sp or not all(c.isalnum() or c == "-" for c in app_sp):
        raise SystemExit(f"not an application ID: {app_sp!r}")
    who = f"`{app_sp}`"
    return [f"GRANT USE SCHEMA ON SCHEMA {names['ai']} TO {who}",
            f"GRANT SELECT ON TABLE {names['ai']}.knowledge_index TO {who}",
            f"GRANT SELECT ON TABLE {names['sales']}.rep_account TO {who}"]


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "index":
        from quote_ai.llm import endpoints
        from quote_ai.search import index
        print("synced", index.sync(names["ai"], "knowledge", a.embedding_endpoint or endpoints.EMBED, a.endpoint))
        return
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    if a.command == "setup":
        for stmt in sql_files.load(SRC / "quote_ai" / "search" / "knowledge_tables.sql", **names):
            spark.sql(stmt)
    elif a.command == "accounts":
        print("rep accounts:", accounts(spark, names))
    elif a.command == "chunk":
        print(chunk(spark, names))
    elif a.command == "grants":
        for stmt in app_grants(names, a.app_sp):
            spark.sql(stmt)


if __name__ == "__main__":
    main()
