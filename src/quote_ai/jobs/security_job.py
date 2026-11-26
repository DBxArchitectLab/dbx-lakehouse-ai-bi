"""Job entry point for AI security (AI-24): resources/quote_ai/quote_ai_security_setup.job.yml.

  filters   the row filters (src/quote_ai/rules/sql/row_filters.sql): account_filter on quote_fact and order_line,
            rep_filter on rep_account
  masks     the column masks (rules/sql/column_masks.sql): mask_cost on unit_cost. The price book view masks its own
            cost (product/sku_model.sql, re-run by quote_ai_product_model setup)
  grants    the sales groups' SELECT (rules/sql/grants.sql), the per-tool EXECUTE grants per specialist principal
            (config/quote_ai/tool_grants.yml, quote_ai.agents.grants), refused when the file is not least privilege
  audit     who asked and which identity ran each query, from the system tables (prints; the investigation view)

Common: --catalog, --schemas logical=physical,... (from ${resources.schemas.<key>.name}), --reps, --managers.
Needs Databricks (Spark); the statements are plain Python, tested in tests/quote_ai/test_security.py.
Verify before recording: system.access.audit and system.query.history column names and retention in the workspace.
"""
import argparse
import inspect
import json
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.storage import sql_files  # noqa: E402

SQL = SRC / "quote_ai" / "rules" / "sql"


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["filters", "masks", "grants", "audit"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--reps", default="DBxLab_Sales_Reps")
    p.add_argument("--managers", default="DBxLab_Sales_Managers")
    p.add_argument("--principals", default="", help="specialist=principal,... (each specialist's own identity)")
    p.add_argument("--app-name", default="", help="audit: the app whose queries to list")
    return p.parse_args(argv)


def filter_statements(names: dict) -> list[str]:
    return sql_files.load(SQL / "row_filters.sql", **names)


def mask_statements(names: dict) -> list[str]:
    return sql_files.load(SQL / "column_masks.sql", **names)


def grant_statements(names: dict, reps: str, managers: str, principals: dict[str, str]) -> list[str]:
    from quote_ai.agents import grants
    problems = grants.findings()
    if problems:
        raise SystemExit("tool_grants.yml is not least privilege: " + "; ".join(problems))
    for g in (reps, managers):
        if not g or not all(c.isalnum() or c in "_- ." for c in g):
            raise SystemExit(f"not a group name: {g!r}")
    text = (SQL / "grants.sql").read_text(encoding="utf-8").replace("{REPS}", reps).replace("{MANAGERS}", managers)
    return sql_files.statements(sql_files.render(text, **names)) + grants.statements(names["ai"], principals)


def audit_sql(app_name: str, days: int = 7) -> str:
    """Who asked, and as which identity each statement ran: a rep's queries show the rep as executed_by (user
    authorization); the app's own show its service principal. Verify before recording: system.query.history columns
    (executed_by, client_application, statement_text, start_time)."""
    if not all(c.isalnum() or c in "-_" for c in app_name):
        raise ValueError(f"not an app name: {app_name!r}")
    return (f"SELECT start_time, executed_by, client_application, left(statement_text, 200) AS statement "
            f"FROM system.query.history WHERE start_time > current_timestamp() - INTERVAL {int(days)} DAYS "
            f"AND client_application ILIKE '%{app_name}%' ORDER BY start_time DESC LIMIT 200")


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "grants":
        stmts = grant_statements(names, a.reps, a.managers,
                                 dict(p.split("=", 1) for p in a.principals.split(",") if p))
    elif a.command == "filters":
        stmts = filter_statements(names)
    elif a.command == "masks":
        stmts = mask_statements(names)
    else:
        stmts = [audit_sql(a.app_name or "quote-ai")]
    from pyspark.sql import SparkSession
    spark = SparkSession.builder.getOrCreate()
    for s in stmts:
        rows = spark.sql(s).collect()
        if a.command == "audit":
            print(json.dumps([r.asDict() for r in rows], default=str, indent=1))
    print(a.command, len(stmts), "statements")


if __name__ == "__main__":
    main()
