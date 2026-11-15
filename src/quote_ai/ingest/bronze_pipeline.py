"""The bronze pipeline (Lakeflow declarative pipeline): one keyed bronze table per file table in the config.

For each table: Auto Loader reads new files from the landing volume (checkpointed: a file is read once), columns are
normalized to snake_case, the lineage columns are added, the expectations applied, and an AUTO CDC flow keeps one row
per key, the latest release winning. A replay, or a device in both the full release and an update, can't duplicate.
New columns are added (schema evolution); rows that don't fit go to _rescued_data instead of failing the load.

Pipeline configuration (set by the bundle, resources/quote_ai/quote_ai_ingest_bronze.pipeline.yml):
  quote_ai.src_path       the bundle's src folder, so quote_ai imports
  quote_ai.config_path    config/quote_ai/ingest/sources.yml
  quote_ai.catalog        the environment's catalog
  quote_ai.schemas        logical=physical schema names, comma-separated (dev prefixes them per developer)
"""
import sys

import dlt  # noqa: F401  (Lakeflow: the pyspark.pipelines module on newer runtimes; verify before recording)
from pyspark.sql import functions as F

conf = spark.conf  # noqa: F821  (spark is provided by the pipeline runtime)
sys.path.insert(0, conf.get("quote_ai.src_path"))

from quote_ai.ingest.config import load_config  # noqa: E402
from quote_ai.ingest.engine import Names  # noqa: E402
from quote_ai.ingest.lineage import load_id_for, normalized_columns  # noqa: E402

CFG = load_config(conf.get("quote_ai.config_path"))
NAMES = Names(catalog=conf.get("quote_ai.catalog"),
              schemas=dict(p.split("=", 1) for p in conf.get("quote_ai.schemas", "").split(",") if p))
auto_cdc = getattr(dlt, "create_auto_cdc_flow", None) or dlt.apply_changes
EVOLUTION = {"add_new_columns": "addNewColumns", "rescue": "rescue"}


def add_lineage(df, source):
    """_source, _file_or_request, _release_date, _load_id, _ingested_at: on every bronze row. The load ID comes from
    the landing folder (<yyyymmdd>), so a replayed file keeps its load ID."""
    tag = F.regexp_extract(F.col("_metadata.file_path"), r"/(\d{8})/[^/]+$", 1)
    return (df.withColumn("_source", F.lit(source.name))
              .withColumn("_file_or_request", F.col("_metadata.file_path"))
              .withColumn("_release_date", F.to_date(tag, "yyyyMMdd"))
              .withColumn("_load_id", F.concat(F.lit(load_id_for(source.name, "")), tag))
              .withColumn("_ingested_at", F.current_timestamp()))


def with_expectations(fn, table):
    """The table's expectations from the config: warn, drop or fail."""
    for decorate, action in ((dlt.expect_all, "warn"), (dlt.expect_all_or_drop, "drop"),
                             (dlt.expect_all_or_fail, "fail")):
        rules = {k: e.rule for k, e in table.expectations.items() if e.action == action}
        if rules:
            fn = decorate(rules)(fn)
    return fn


def bronze_table(source, table):
    """One target per configured table: Auto Loader -> snake_case -> lineage -> expectations -> keyed AUTO CDC."""
    target = NAMES.table(table.target)
    view = f"{source.name}__{table.table_name}__landed"
    root = NAMES.volume_path(source.volume) / source.landing

    def landed():                                                   # 1 Auto Loader: each file read once
        df = (spark.readStream.format("cloudFiles")  # noqa: F821
              .option("cloudFiles.format", source.format)
              .option("cloudFiles.schemaEvolutionMode", EVOLUTION[table.schema_evolution])
              .option("cloudFiles.inferColumnTypes", "false")     # bronze keeps values as published: strings
              .options(**source.options, **table.options)
              .load(f"{root}/*/{table.pattern}"))
        df = df.toDF(*normalized_columns(df.columns))               # 2 snake_case column names
        return add_lineage(df, source)                              # 3 lineage on every row

    landed = with_expectations(landed, table)                       # 4 expectations from the config
    dlt.view(name=view, comment=f"{source.name}: {table.pattern} as landed")(landed)

    dlt.create_streaming_table(name=target, comment=table.comment or source.description,   # 5 keyed AUTO CDC
                               table_properties={f"quote_ai.{k}": v for k, v in
                                                 {"source": source.name, "owner": source.owner,
                                                  **table.tags}.items()})
    auto_cdc(target=target, source=view, keys=table.keys, sequence_by=F.col(table.sequence_by),
             stored_as_scd_type=1)


for s in CFG.enabled(type="file"):
    for t in s.tables:
        bronze_table(s, t)
