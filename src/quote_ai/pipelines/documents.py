"""Supplier documents to quality evidence (AI-09), a Lakeflow Spark Declarative Pipeline: parse, extract, verify.

  parsed_documents           streaming table: every new PDF in the documents volume (Auto Loader, binaryFile),
                             parsed once with ai_parse_document; the parsed output is stored, so extraction can rerun
                             on its own when the prompt changes
  document_values            the fields an AI Function read from the parsed text: document ID, version, catalog
                             numbers, AQL, thickness, tensile strength, drugs, standard, results
  supplier_quality_evidence  per document and SKU, the evidence rows with their status: verified only when all five
                             checks pass (registered, current, this product, fields, QA sign-off), else unverified
                             with the failing checks; rows of an older version superseded
                             (quote_ai.product.evidence.document_evidence, the tested code)

The registry (quote_ai_product.document_registry) and the QA sign-offs (document_qa_signoff) are Delta tables written
by src/quote_ai/jobs/documents_job.py; the this-product check reads the verified SKU mappings (AI-03). Requests
(requests/) are parsed by the app, not here. The model extracts; only document_evidence decides a status.

Pipeline configuration (resources/quote_ai/quote_ai_documents.pipeline.yml):
  quote_ai.src_path, quote_ai.catalog, quote_ai.schemas, quote_ai.documents_path (/Volumes/.../quote_ai_documents)

Verify before recording: ai_parse_document's output (the VARIANT path to the elements and their content), ai_extract
over long text, cloudFiles with binaryFile in a serverless pipeline, and the module name (dlt or pyspark.pipelines).
"""
import sys
from datetime import datetime, timezone

import dlt
from pyspark.sql import functions as F
from pyspark.sql import types as T

conf = spark.conf  # noqa: F821
sys.path.insert(0, conf.get("quote_ai.src_path"))

from quote_ai.product import evidence  # noqa: E402
from quote_ai.storage import sql_files  # noqa: E402

N = sql_files.names_for(conf.get("quote_ai.catalog"),
                        dict(p.split("=", 1) for p in conf.get("quote_ai.schemas", "").split(",") if p))
ROOT = conf.get("quote_ai.documents_path")
FOLDERS = ("specs", "test_reports", "marketing")
PARSED_TEXT = evidence.PARSED_TEXT_SQL     # the parsed elements flattened to text (shared with the app's upload)
LABELS = ["document_id", "version", "supplier", "catalog_numbers", "aql", "thickness", "tensile_strength",
          "drugs", "standard", "results", "chemo_claim", "material", "powder_free"]   # material, powder_free: AI-15


@dlt.table(comment="Supplier documents (synthetic demo data) as parsed by ai_parse_document: text, tables and pages, "
                   "stored once per file.")
def parsed_documents():
    return (spark.readStream.format("cloudFiles")  # noqa: F821
            .option("cloudFiles.format", "binaryFile").option("pathGlobFilter", "*.pdf")
            .option("recursiveFileLookup", "true")
            .load(f"{ROOT}/")
            .where(F.regexp_extract("path", r"/(" + "|".join(FOLDERS) + r")/", 1) != "")
            .select("path", "modificationTime", F.expr("ai_parse_document(content)").alias("parsed")))


@dlt.table(comment="The fields an AI Function read from each parsed supplier document. Extracted values: nothing here "
                   "is verified.")
def document_values():
    labels = ", ".join(f"'{l}'" for l in LABELS)
    return (spark.read.table("parsed_documents")  # noqa: F821
            .select("path", F.expr(PARSED_TEXT).alias("text"))
            .select("path", F.expr(f"ai_extract(text, array({labels}))").alias("v"),
                    F.current_timestamp().alias("extracted_at")))


SCHEMA = T.StructType([T.StructField(f, t) for f, t in [
    ("di", T.StringType()), ("product_id", T.StringType()), ("sku_id", T.StringType()),
    ("attribute_name", T.StringType()), ("attribute_value", T.StringType()), ("unit", T.StringType()),
    ("source_type", T.StringType()), ("source_id", T.StringType()), ("document_id", T.StringType()),
    ("document_version", T.IntegerType()), ("test_method", T.StringType()), ("extraction_method", T.StringType()),
    ("verification_status", T.StringType()), ("reason", T.StringType()), ("verified_by", T.StringType()),
    ("verified_at", T.TimestampType()), ("observed_at", T.TimestampType())]])


def _skus() -> list:
    """Every SKU with its verified mapping's catalog number (unverified mappings fail the this-product check).
    The mapping carries no supplier name yet, so the check compares catalog numbers."""
    rows = spark.sql(f"""
        SELECT s.sku_id, s.product_id, s.supplier_catalog_number AS catalog_no,
               EXISTS(SELECT 1 FROM {N['product']}.product_device_mapping m
                      WHERE m.sku_id = s.sku_id AND m.verification_status = 'verified') AS mapping_verified
        FROM {N['product']}.dbxlab_sku s""").collect()  # noqa: F821
    return [evidence.SkuRef(sku_id=r.sku_id, product_id=r.product_id, supplier=None, catalog_no=r.catalog_no,
                            mapping_verified=bool(r.mapping_verified)) for r in rows]


@dlt.table(name=f"{N['product']}.supplier_quality_evidence",
           comment="Quality evidence from supplier documents (demo data): every value with its document ID and "
                   "version, unit, test method and status. Verified only when all five checks pass and are recorded; "
                   "otherwise unverified with the failing checks in reason; rows of an older version superseded.")
def supplier_quality_evidence():
    registry = [r.asDict() for r in spark.read.table(f"{N['product']}.document_registry").collect()]  # noqa: F821
    values = {r.path: (r.v.asDict() if r.v is not None else {})
              for r in spark.read.table("document_values").collect()}  # noqa: F821
    qa = {(r.document_id, int(r.document_version)): (r.verified_by, r.verified_at)
          for r in spark.read.table(f"{N['product']}.document_qa_signoff").collect()}  # noqa: F821
    docs = [evidence.to_document(r, values.get(r["path"]), registry) for r in registry
            if r["path"] in values and r["document_type"] in ("spec_sheet", "test_report", "marketing")]
    rows = evidence.document_evidence(docs, _skus(), qa, datetime.now(timezone.utc))
    data = [tuple(r.table_row().get(f) for f in SCHEMA.fieldNames()) for r in rows]
    return spark.createDataFrame(data, schema=SCHEMA)  # noqa: F821
