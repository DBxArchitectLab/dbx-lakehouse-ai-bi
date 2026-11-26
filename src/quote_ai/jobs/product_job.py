"""Job entry point for DBxLab's product model (AI-03): one command per task of resources/quote_ai/quote_ai_product_model.job.yml.

  setup    the product and sales tables and views (src/quote_ai/product/sku_model.sql), the glove views
           (src/quote_ai/pipelines/glove_subset.sql), after the quote_ai_gudid pipeline, and the quote tables
           (src/quote_ai/rules/sql/quote_tables.sql, AI-07)
  seed     DBxLab's synthetic catalog and reps (quote_ai.mock.catalog); deterministic, so a rerun writes the same
           rows. The quote history is written by the win model's job since AI-12 (src/quote_ai/jobs/ml_job.py seed)
  match    bind each SKU to a GUDID glove device (demo binding), auto-match it at every package level, reconcile with
           what reviewers decided (a changed GUDID package -> superseded), MERGE into product_device_mapping
           [--demo-verify]  mark the high-confidence box matches as verified by demo:sales-ops (demo data only;
                            in real use a person verifies in the review queue)
  verify --mapping-id ID --reviewer NAME [--reject REASON]   a person's decision on one mapping
  grant-app --app-sp ID   the QuoteAI app's table grants (AI-07): SELECT on the product model (evidence included,
           AI-08/09), GUDID gold and the price book; SELECT and MODIFY on the quote tables; SELECT on what the Genie
           Agent reads (AI-10/11: metric views, customers, quotes, orders, device sizes). Nothing else.

Common: --catalog and --schemas logical=physical,... (from ${resources.schemas.<key>.name}).
Needs Spark (Databricks); the logic it calls is plain Python, tested in tests/quote_ai/test_device_mapping.py.
"""
import argparse
import inspect
import sys
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.mock import catalog as mock  # noqa: E402
from quote_ai.product import mapping_review as mr  # noqa: E402
from quote_ai.storage import sql_files  # noqa: E402

DEMO_REVIEWER = "demo:sales-ops"


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["setup", "seed", "match", "verify", "grant-app"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--demo-verify", action="store_true")
    p.add_argument("--mapping-id")
    p.add_argument("--reviewer")
    p.add_argument("--reject")
    p.add_argument("--app-sp")
    return p.parse_args(argv)


def run_sql_file(spark, path: Path, names: dict) -> None:
    for stmt in sql_files.load(path, **names):
        spark.sql(stmt)


def _df(spark, rows: list[dict], fqn: str):
    """Rows as a DataFrame with the table's own schema and column order (no type inference on null columns)."""
    from decimal import Decimal

    from pyspark.sql.types import DecimalType
    schema = spark.table(fqn).schema

    def value(f, v):
        return Decimal(str(v)) if isinstance(f.dataType, DecimalType) and isinstance(v, float) else v
    return spark.createDataFrame([tuple(value(f, r.get(f.name)) for f in schema.fields) for r in rows],
                                 schema=schema)


def _write(spark, rows: list[dict], fqn: str) -> None:
    """Overwrite a demo table with generated rows (deterministic, so this is idempotent)."""
    _df(spark, rows, fqn).write.mode("overwrite").insertInto(fqn)


MAPPING_COLUMNS = list(mr.DeviceMapping.model_fields)


def seed(spark, names: dict) -> None:
    _write(spark, mock.products(), f"{names['product']}.dbxlab_product")
    _write(spark, mock.reps(), f"{names['sales']}.rep")


def match(spark, names: dict, demo_verify: bool) -> dict:
    devices = [r.asDict() for r in spark.sql(
        f"SELECT primary_di, catalog_number FROM {names['gudid_gold']}.glove_devices").collect()]
    packages = [r.asDict() for r in spark.sql(
        f"SELECT primary_di, package_di, contains_di, pkg_quantity FROM {names['gudid_gold']}.glove_packages"
    ).collect()]
    levels = mr.levels_by_device(packages)
    candidates = [{"primary_di": d["primary_di"], "catalog_number": d["catalog_number"],
                   "box_quantity": mr.box_quantity(levels.get(str(d["primary_di"]), []), str(d["primary_di"]))}
                  for d in devices]
    skus = mock.bind_devices(mock.skus(), candidates)
    _write(spark, skus, f"{names['product']}.dbxlab_sku")

    existing = [mr.DeviceMapping(**{k: v for k, v in r.asDict().items() if k in MAPPING_COLUMNS})
                for r in spark.sql(f"SELECT * FROM {names['product']}.product_device_mapping").collect()]
    mappings = mr.match_catalog(skus, devices, packages, existing)
    if demo_verify:
        now = datetime.now(timezone.utc)
        mappings = [mr.verify(m, DEMO_REVIEWER, now)
                    if m.verification_status == mr.Status.UNVERIFIED and m.mapping_confidence == mr.Confidence.HIGH
                    else m for m in mappings]
    rows = [m.model_dump(mode="python") | {"updated_at": datetime.now(timezone.utc)} for m in mappings]
    for r in rows:
        r["mapping_confidence"], r["verification_status"] = str(r["mapping_confidence"].value), \
            str(r["verification_status"].value)
    if rows:
        _df(spark, rows, f"{names['product']}.product_device_mapping").createOrReplaceTempView("new_mappings")
        spark.sql(f"""MERGE INTO {names['product']}.product_device_mapping t
                      USING new_mappings s ON t.mapping_id = s.mapping_id
                      WHEN MATCHED THEN UPDATE SET *
                      WHEN NOT MATCHED THEN INSERT *""")
    queue = mr.review_queue(mappings)
    return {"skus": len(skus), "bound": sum(1 for s in skus if s["supplier_di"]), "mappings": len(mappings),
            "offerable": sum(mr.offerable(m) for m in mappings), "review_queue": len(queue)}


def verify(spark, names: dict, mapping_id: str, reviewer: str, reject: str | None) -> None:
    rows = spark.sql(f"SELECT * FROM {names['product']}.product_device_mapping WHERE mapping_id = :id",
                     args={"id": mapping_id}).collect()
    if not rows:
        raise SystemExit(f"no mapping {mapping_id}")
    m = mr.DeviceMapping(**{k: v for k, v in rows[0].asDict().items() if k in MAPPING_COLUMNS})
    now = datetime.now(timezone.utc)
    m = mr.reject(m, reviewer, now, reject) if reject else mr.verify(m, reviewer, now)
    spark.sql(f"""UPDATE {names['product']}.product_device_mapping
                  SET verification_status = :status, verified_by = :by, verified_at = :at, reason = :reason,
                      mapping_method = :method, updated_at = :at
                  WHERE mapping_id = :id""",
              args={"status": m.verification_status.value, "by": reviewer, "at": now, "reason": m.reason,
                    "method": m.mapping_method, "id": mapping_id})


def app_grants(names: dict, app_sp: str) -> list[str]:
    """Least privilege for the app's service principal (an application ID).

    AI-24: the back end reads a rep's data as the rep (user authorization), so the app's own identity keeps only what
    the app itself needs: the product model and GUDID gold (the same for every rep), the account list the knowledge
    retrieval filters by (rep_account, AI-17), SELECT on the Delta copy of the quotes (live quotes are on Lakebase since
    AI-20: no MODIFY here), and the evaluation schema (feedback candidates, results). It no longer reads the price
    book, the sales facts with unit_cost, or the metric views with margin_pct: those are the reps' own reads, filtered
    and masked by Unity Catalog. REVOKE_FROM_APP takes back what AI-07 to AI-11 granted."""
    if not app_sp or not all(c.isalnum() or c == "-" for c in app_sp):
        raise SystemExit(f"not an application ID: {app_sp!r}")
    who = f"`{app_sp}`"
    out = [f"GRANT USE CATALOG ON CATALOG {names['catalog']} TO {who}"]
    for key in ("product", "gudid_gold", "sales", "eval"):
        out.append(f"GRANT USE SCHEMA ON SCHEMA {names[key]} TO {who}")
    out += [f"GRANT SELECT ON SCHEMA {names['product']} TO {who}",
            f"GRANT SELECT ON SCHEMA {names['gudid_gold']} TO {who}"]
    out += [f"GRANT SELECT ON TABLE {names['sales']}.{t} TO {who}" for t in ("quote", "quote_line", "quote_event")]
    out += [f"GRANT SELECT ON TABLE {names['sales']}.{t} TO {who}" for t in APP_SALES_READ]
    out += [f"GRANT SELECT, MODIFY ON TABLE {names['eval']}.candidate_cases TO {who}",        # AI-22: feedback
            f"GRANT SELECT ON TABLE {names['eval']}.eval_results TO {who}"]
    out += [f"REVOKE {priv} ON TABLE {names[schema]}.{t} FROM {who}" for priv, schema, t in REVOKE_FROM_APP]
    return out


APP_SALES_READ = ("rep_account",)        # the knowledge filter by account (AI-17), read as the app
# AI-24: what the app's identity had and a rep's token now covers (the gaps: unit_cost, margin_pct, every account)
REVOKE_FROM_APP = [("MODIFY", "sales", "quote"), ("MODIFY", "sales", "quote_line"), ("MODIFY", "sales", "quote_event"),
                   ("SELECT", "sales", "price_book"), ("SELECT", "sales", "customer"),
                   ("SELECT", "sales", "quote_metrics"), ("SELECT", "sales", "order_metrics"),
                   ("SELECT", "sales", "quote_fact"), ("SELECT", "sales", "order_line"),
                   ("SELECT", "gudid_silver", "device_size")]
SALES_READ = ()                          # AI-10, AI-11: Genie as the app; gone in AI-24 (Genie asks as the rep)


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    schemas = dict(p.split("=", 1) for p in a.schemas.split(",") if p)
    names = sql_files.names_for(a.catalog, schemas)
    if a.command == "setup":
        run_sql_file(spark, SRC / "quote_ai" / "pipelines" / "glove_subset.sql", names)
        run_sql_file(spark, SRC / "quote_ai" / "product" / "sku_model.sql", names)
        run_sql_file(spark, SRC / "quote_ai" / "rules" / "sql" / "quote_tables.sql", names)   # AI-07
    elif a.command == "seed":
        seed(spark, names)
    elif a.command == "match":
        print(match(spark, names, a.demo_verify))
    elif a.command == "verify":
        if not a.mapping_id or not a.reviewer:
            raise SystemExit("verify needs --mapping-id and --reviewer")
        verify(spark, names, a.mapping_id, a.reviewer, a.reject)
    elif a.command == "grant-app":
        for stmt in app_grants(names, a.app_sp):
            spark.sql(stmt)


if __name__ == "__main__":
    main()
