"""GUDID gold (AI-03), in the same Lakeflow pipeline as silver: what QuoteAI reads, in quote_ai_gudid_gold.

  device_distribution  per record: its current version, whether it is in commercial distribution, and the day it
                       left (from device_history, SCD type 2). A device that left distribution is a fact the agent
                       needs later; overwriting silver with the latest file would lose it.

The glove subset itself (glove_devices, glove_packages) is a view on AI-02's classification:
src/quote_ai/pipelines/glove_subset.sql, created by the product job after this pipeline runs.

Verify before recording: publishing to a second schema from one pipeline (fully qualified names, default publishing
mode), and the __START_AT / __END_AT column names of SCD type 2 targets.
"""
import sys

import dlt
from pyspark.sql import functions as F

conf = spark.conf  # noqa: F821
sys.path.insert(0, conf.get("quote_ai.src_path"))

from quote_ai.ingest.engine import Names  # noqa: E402

NAMES = Names(catalog=conf.get("quote_ai.catalog"),
              schemas=dict(p.split("=", 1) for p in conf.get("quote_ai.schemas", "").split(",") if p))


@dlt.table(name=NAMES.table("quote_ai_gudid_gold.device_distribution"),
           comment="GUDID devices and their commercial distribution over time (public data): the current version, "
                   "in distribution or not, and when it left. From silver device_history.")
def device_distribution():
    h = spark.read.table("device_history")  # noqa: F821  (silver, same pipeline)
    current = h.where(F.col("__END_AT").isNull())
    left = (h.where(~F.col("in_commercial_distribution"))
             .groupBy("public_device_record_key")
             .agg(F.min("public_version_date").alias("left_distribution_on")))
    return (current.select("primary_di", "public_device_record_key", "public_version_number", "brand_name",
                           "catalog_number", "device_record_status", "in_commercial_distribution",
                           "device_comm_distribution_end_date", "_release_date")
            .join(left, "public_device_record_key", "left"))
