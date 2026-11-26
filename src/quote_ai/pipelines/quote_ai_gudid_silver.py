"""GUDID silver (AI-03), a Lakeflow Spark Declarative Pipeline: AI-02's bronze, typed, with every version kept.

  device_versions  every published version of a device, from the full release (its current version), the daily
                   updates and the API history, with lineage
  device_history   all versions per record, SCD type 2 (AUTO CDC): when it changed, when it left distribution
  device           the current version per record, SCD type 1 (one row per device)
  package          every identifier of a device: package DI, what it contains, how many (identifiers_raw)
  gmdn_term, product_code, device_size   the child tables

Bronze is AI-02's and is not rebuilt here: this pipeline only reads it (streaming, so only new rows are processed).
Pipeline configuration (resources/quote_ai/quote_ai_gudid.pipeline.yml):
  quote_ai.src_path   the bundle's src folder;  quote_ai.catalog;  quote_ai.schemas  logical=physical,...

Verify before recording: the module name (dlt or pyspark.pipelines), create_auto_cdc_flow and its ignore_null_updates,
and the daily XML's top-level column names after snake_case (nested identifiers are read from bronze in AI-02's
classifier, not here).
"""
import sys

import dlt
from pyspark.sql import functions as F

conf = spark.conf  # noqa: F821  (provided by the pipeline runtime)
sys.path.insert(0, conf.get("quote_ai.src_path"))

from quote_ai.ingest.engine import Names  # noqa: E402

NAMES = Names(catalog=conf.get("quote_ai.catalog"),
              schemas=dict(p.split("=", 1) for p in conf.get("quote_ai.schemas", "").split(",") if p))
BRONZE = "quote_ai_gudid_bronze"
auto_cdc = getattr(dlt, "create_auto_cdc_flow", None) or dlt.apply_changes

# The device attributes QuoteAI reads, as snake_case bronze columns (AccessGUDID device.txt).
DEVICE_COLUMNS = [
    "primary_di", "public_device_record_key", "public_version_status", "device_record_status",
    "public_version_date", "device_publish_date", "device_comm_distribution_status",
    "device_comm_distribution_end_date", "brand_name", "version_model_number", "catalog_number", "company_name",
    "device_count", "device_description", "labeled_contains_nrl", "labeled_no_nrl", "device_sterile",
    "sterilization_prior_to_use", "single_use", "rx", "otc",
]
LINEAGE = ["_source", "_load_id", "_release_date"]


def bronze(table: str):
    """A bronze table as a stream. Bronze is keyed by AUTO CDC/MERGE, so a replay rewrites rows in place: those
    change commits carry nothing new (same key, same version) and are skipped. A new device version arrives as a new
    key in daily_device_raw and api_device_history (keyed by record key and version), so it is read.
    Verify: a re-loaded full release (device_raw keyed by DI) updates in place and is skipped here; rebuild with a
    full refresh of this pipeline after loading a new full release."""
    return (spark.readStream.option("skipChangeCommits", "true")  # noqa: F821
            .table(NAMES.table(f"{BRONZE}.{table}")))


def pick(df, columns):
    """The listed columns as strings, null where a source doesn't have one (the API carries only version fields)."""
    have = set(df.columns)
    return [(F.col(c).cast("string") if c in have else F.lit(None).cast("string")).alias(c) for c in columns]


def versions(df):
    return df.select(*pick(df, DEVICE_COLUMNS),
                     F.col("public_version_number").cast("int").alias("public_version_number"),
                     *[F.col(c) for c in LINEAGE])


@dlt.view(comment="Every published version of a device: full release, daily updates and API history, unioned")
def device_versions():
    # full release: the current version; daily files and the API: every version, in order
    return (versions(bronze("device_raw"))
            .unionByName(versions(bronze("daily_device_raw")))
            .unionByName(versions(bronze("api_device_history")))
            .where("public_device_record_key IS NOT NULL AND public_version_number IS NOT NULL")
            .withColumn("in_commercial_distribution",
                        F.coalesce(F.upper(F.col("device_comm_distribution_status")) == "IN COMMERCIAL DISTRIBUTION",
                                   F.col("device_comm_distribution_end_date").isNull())))


SEQUENCE = F.struct("public_version_number", "_release_date")

dlt.create_streaming_table(
    "device_history",
    comment="GUDID devices, every published version (SCD type 2): when a record changed and when it left "
            "commercial distribution. Source truth, public data.")
auto_cdc(target="device_history", source="device_versions", keys=["public_device_record_key"],
         sequence_by=SEQUENCE, stored_as_scd_type=2, ignore_null_updates=True)

dlt.create_streaming_table(
    "device",
    comment="GUDID devices, the current version per record (one row per device). Source truth, public data: a "
            "description is what the labeler wrote, not a verified fact.")
auto_cdc(target="device", source="device_versions", keys=["public_device_record_key"],
         sequence_by=SEQUENCE, stored_as_scd_type=1, ignore_null_updates=True)


@dlt.table(comment="Every identifier of a device: the package DI, the DI it contains and how many (pkg_quantity). "
                   "The input to gloves per unit.")
def package():
    df = bronze("identifiers_raw")
    return df.select(
        F.col("primary_di"), F.col("device_id").alias("package_di"), *pick(df, ["device_id_type", "pkg_type",
                                                                                "pkg_status"]),
        F.col("contains_di_number").alias("contains_di") if "contains_di_number" in df.columns
        else F.lit(None).cast("string").alias("contains_di"),
        F.col("pkg_quantity").cast("int").alias("pkg_quantity") if "pkg_quantity" in df.columns
        else F.lit(None).cast("int").alias("pkg_quantity"),
        *[F.col(c) for c in LINEAGE])


@dlt.table(comment="GMDN preferred terms of each device")
def gmdn_term():
    df = bronze("gmdn_terms_raw")
    return df.select("primary_di", *pick(df, ["gmdn_pt_name", "gmdn_pt_definition", "gmdn_code"]),
                     *[F.col(c) for c in LINEAGE])


@dlt.table(comment="FDA product codes of each device (LZA, LZC, LYY for gloves)")
def product_code():
    df = bronze("product_codes_raw")
    return df.select("primary_di", *pick(df, ["product_code", "product_code_name"]), *[F.col(c) for c in LINEAGE])


@dlt.table(comment="Clinically relevant sizes of each device (glove sizes S to XL)")
def device_size():
    df = bronze("device_sizes_raw")
    return df.select("primary_di", *pick(df, ["size_type", "size_unit", "size_value", "size_text"]),
                     *[F.col(c) for c in LINEAGE])
