"""Glove attributes with evidence (AI-08), a Lakeflow Spark Declarative Pipeline: AI Functions over the glove subset.

  ai_glove_attributes   streaming table: every new GUDID glove device version, read once (incremental), with the
                        attribute form an AI Function filled from its description
  glove_attributes      the latest extraction per device (materialized view)
  product_evidence      every attribute value with source, method and status: structured GUDID fields verified, AI
                        values unverified, disagreements conflicting (quote_ai.product.evidence.evidence_rows)
  attribute_review      the conflicting attributes, for a person (materialized view)

Filter first, enrich second: only devices AI-02 classified as gloves (quote_ai_ops.glove_devices), never all of GUDID.
Incremental: the source is silver device_history (SCD type 2), where a new or changed device is a new row; change
commits (an old version closing) are skipped, so a rerun calls the model only for what is new.
Model access: ai_query goes to the Unity Gateway extraction service (quote_ai_extract_<target>); the pipeline's
identity (sp-dbx-lakehouse in dev-shared and prod) holds EXECUTE on it.

Pipeline configuration (resources/quote_ai/quote_ai_attributes.pipeline.yml):
  quote_ai.src_path, quote_ai.catalog, quote_ai.schemas (logical=physical,...)
  quote_ai.extract_service   the model service ai_query calls
  quote_ai.method            ai_extract (default, task function) or ai_query (typed JSON schema, when ai_extract is
                             too loose)

Verify before recording: ai_extract's signature and output (a struct of strings named by the labels), ai_query's
responseFormat argument, whether either runs inside a streaming table of a serverless pipeline, and the module name
(dlt or pyspark.pipelines).
"""
import sys
from datetime import datetime, timezone

import dlt
from pyspark.sql import functions as F
from pyspark.sql import types as T

conf = spark.conf  # noqa: F821  (provided by the pipeline runtime)
sys.path.insert(0, conf.get("quote_ai.src_path"))

from quote_ai.llm import prompts  # noqa: E402
from quote_ai.llm.attributes import response_format_sql  # noqa: E402
from quote_ai.product import evidence  # noqa: E402
from quote_ai.storage import sql_files  # noqa: E402

N = sql_files.names_for(conf.get("quote_ai.catalog"),
                        dict(p.split("=", 1) for p in conf.get("quote_ai.schemas", "").split(",") if p))
SERVICE = conf.get("quote_ai.extract_service", "quote_ai_extract_dev")
METHOD = conf.get("quote_ai.method", "ai_extract")
PROMPT_VERSION = "v1"
LABELS = ", ".join(f"'{f}'" for f in evidence.EXTRACT_FIELDS)
STRUCTURED = ["labeled_contains_nrl", "labeled_no_nrl", "device_sterile", "in_commercial_distribution"]


def extraction_sql() -> str:
    """The AI Function, as a SQL expression over device_description. One place, so the method is a config switch."""
    if METHOD == "ai_query":
        prompt = prompts.ATTRIBUTES_V1.split("<description>")[0].replace("'", "''")
        return (f"from_json(ai_query('{SERVICE}', concat('{prompt}', '<description>', device_description, "
                f"'</description>'), responseFormat => '{response_format_sql()}'), "
                f"'material STRING, powder_free STRING, textured STRING, length_in STRING, chemo_claim STRING, "
                f"use_type STRING, latex_free STRING')")
    return f"ai_extract(device_description, array({LABELS}))"


@dlt.table(comment="Glove device versions with the attributes an AI Function read from the description. Extracted "
                   "claims, unverified: every value is a claim until evidence confirms it (AI-08).")
def ai_glove_attributes():
    gloves = spark.read.table(f"{N['ops']}.glove_devices").select("primary_di").distinct()  # noqa: F821
    versions = (spark.readStream.option("skipChangeCommits", "true")  # noqa: F821
                .table(f"{N['gudid_silver']}.device_history")
                .where("device_description IS NOT NULL")
                .join(gloves, "primary_di"))
    return versions.select(
        F.col("primary_di").alias("di"), "public_version_number", "device_description", *STRUCTURED,
        F.expr(extraction_sql()).alias("extracted"),
        F.lit(METHOD).alias("extraction_function"), F.lit(PROMPT_VERSION).alias("prompt_version"),
        F.current_timestamp().alias("observed_at"))


@dlt.table(comment="The latest extraction per glove device (one row per DI).")
def glove_attributes():
    df = spark.read.table("ai_glove_attributes")  # noqa: F821
    latest = df.groupBy("di").agg(F.max("public_version_number").alias("public_version_number"))
    return df.join(latest, ["di", "public_version_number"])


EVIDENCE_SCHEMA = T.StructType([
    T.StructField("di", T.StringType()), T.StructField("product_id", T.StringType()),
    T.StructField("sku_id", T.StringType()), T.StructField("attribute_name", T.StringType()),
    T.StructField("attribute_value", T.StringType()), T.StructField("unit", T.StringType()),
    T.StructField("source_type", T.StringType()), T.StructField("source_id", T.StringType()),
    T.StructField("document_id", T.StringType()), T.StructField("document_version", T.IntegerType()),
    T.StructField("test_method", T.StringType()), T.StructField("extraction_method", T.StringType()),
    T.StructField("verification_status", T.StringType()), T.StructField("reason", T.StringType()),
    T.StructField("verified_by", T.StringType()), T.StructField("verified_at", T.TimestampType()),
    T.StructField("observed_at", T.TimestampType()),
])


def _evidence(records) -> list[tuple]:
    """Device rows -> evidence rows, built by quote_ai.product.evidence (the tested code), on the driver. The glove
    subset is tens of thousands of rows, so collecting is cheap, and the workers need no copy of the package."""
    out = []
    for r in records:
        extracted = r["extracted"]
        extracted = extracted.asDict() if hasattr(extracted, "asDict") else dict(extracted or {})
        at = r["observed_at"] or datetime.now(timezone.utc)
        fields = {k: r[k] for k in STRUCTURED} | {"product_codes": r["product_codes"]}   # AI-15: exam vs surgical
        for row in evidence.evidence_rows(r["di"], fields, extracted, at,
                                          r["prompt_version"] or "v1", r["product_id"], r["sku_id"]):
            d = row.table_row()
            out.append(tuple(d.get(f) for f in EVIDENCE_SCHEMA.fieldNames()))
    return out


@dlt.table(name=f"{N['product']}.product_evidence",
           comment="Every glove attribute with its evidence: value, unit, source, method and verification status. "
                   "GUDID structured fields are verified; AI-extracted values are unverified; two sources that "
                   "disagree are both conflicting. Only verified rows are business truth (AI-15). Supplier documents "
                   "add their rows in supplier_quality_evidence (AI-09); glove_evidence shows both.")
def product_evidence():
    skus = spark.read.table(f"{N['product']}.dbxlab_sku").select(  # noqa: F821
        F.col("supplier_di").alias("di"), "sku_id", "product_id")
    codes = (spark.read.table(f"{N['ops']}.glove_devices")  # noqa: F821  AI-15: the FDA product codes per device
             .groupBy(F.col("primary_di").alias("di")).agg(F.max("product_codes").cast("string").alias("product_codes")))
    devices = (spark.read.table("glove_attributes").join(skus, "di", "left")  # noqa: F821
               .join(codes, "di", "left").collect())
    return spark.createDataFrame(_evidence(devices), schema=EVIDENCE_SCHEMA)  # noqa: F821


@dlt.table(name=f"{N['product']}.attribute_review",
           comment="Glove attributes whose sources disagree (status conflicting), for a person to review. Nothing "
                   "here is overwritten; both values stay in product_evidence.")
def attribute_review():
    ev = spark.read.table(f"{N['product']}.product_evidence")  # noqa: F821
    return (ev.where("verification_status = 'conflicting'")
            .groupBy("di", "product_id", "sku_id", "attribute_name")
            .agg(F.concat_ws("; ", F.collect_list(F.concat_ws(" ", "attribute_value", "source_type",
                                                              "extraction_method"))).alias("values"),
                 F.max("reason").alias("reason"), F.max("observed_at").alias("observed_at")))
