"""Job entry point for the agent (AI-18 to AI-20): one command per task of
resources/quote_ai/quote_ai_agent_registration.job.yml.

  functions  the quote_ai wheel into the packages volume, then the agent's tools as Unity Catalog functions
             (src/quote_ai/rules/sql/tools.sql; AI-19: gudid_live_lookup.sql). --source-root, --packages,
             --win-endpoint
  register   log QuoteAgent (models from code: src/quote_ai/agents/agent.py) with MLflow and register it as
             <catalog>.<quote_ai_ai>.quote_agent; --experiment
  grants     least privilege for the agent's identity and the sales group: USE SCHEMA, EXECUTE on the tool
             functions only (never a grant on the quote tables), USE CONNECTION on quote_ai_accessgudid (AI-19).
             --principal (the agent's service principal: the app's until AI-20, the agent app's after), --group

Common: --catalog, --schemas logical=physical,... (from ${resources.schemas.<key>.name}).
Needs Databricks (Spark, MLflow); the logic is plain Python, tested in tests/quote_ai/test_tools.py.
Verify before recording: mlflow.pyfunc.log_model(name=..., python_model=<file path>, code_paths=..., resources=...)
and the resource classes (DatabricksServingEndpoint, DatabricksFunction, DatabricksVectorSearchIndex,
DatabricksGenieSpace) in MLflow 3.
"""
import argparse
import inspect
import sys
from pathlib import Path

SRC = Path(inspect.currentframe().f_code.co_filename).resolve().parents[2]
sys.path.insert(0, str(SRC))
from quote_ai.storage import sql_files  # noqa: E402

WHEEL = "__QUOTE_AI_WHEEL__"
TOOL_FUNCTIONS = ["search_candidates", "check_eligibility", "get_evidence", "get_device", "check_fulfilment",
                  "price_quote", "win_probability"]
HELPER_FUNCTIONS = ["eligibility_rules", "fulfilment_rules", "price_rules"]
LIVE_FUNCTIONS = ["gudid_live_lookup"]                     # AI-19
CONNECTION = "quote_ai_accessgudid"                        # AI-19


def parse(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["functions", "register", "grants"])
    p.add_argument("--catalog", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--source-root")
    p.add_argument("--packages")
    p.add_argument("--win-endpoint", default="")
    p.add_argument("--experiment", default="/Shared/quote_ai/agent")
    p.add_argument("--principal", help="the agent's service principal (application ID)")
    p.add_argument("--group", default="", help="the sales reps group (EXECUTE on the tools)")
    p.add_argument("--live", action="store_true", help="AI-19: also gudid_live_lookup and USE CONNECTION")
    return p.parse_args(argv)


def replacements(wheel: str, win_endpoint: str) -> dict[str, str]:
    from quote_ai.rules.policy import load_policy
    policy = load_policy(SRC.parent / "config" / "quote_ai" / "policy.yml")
    return {WHEEL: wheel, "__WIN_ENDPOINT__": win_endpoint, "__REP_LIMIT__": str(policy.pricing.rep_discount_limit)}


def grants(names: dict, principal: str, group: str = "", live: bool = False) -> list[str]:
    """EXECUTE on the tools and their helpers, nothing else: the agent can read through its tools and can't write a
    quote even if a prompt tells it to (it has no grant on quote_ai_sales). The group gets the same tools."""
    out = []
    for who in [w for w in (principal, group) if w]:
        if not all(c.isalnum() or c in "-_ ." for c in who):
            raise SystemExit(f"not a principal name: {who!r}")
        q = f"`{who}`"
        out.append(f"GRANT USE SCHEMA ON SCHEMA {names['ai']} TO {q}")
        for fn in TOOL_FUNCTIONS + HELPER_FUNCTIONS + (LIVE_FUNCTIONS if live else []):
            out.append(f"GRANT EXECUTE ON FUNCTION {names['ai']}.{fn} TO {q}")
        if live and who == principal:
            out.append(f"GRANT USE CONNECTION ON CONNECTION {CONNECTION} TO {q}")
    return out


def register(names: dict, experiment: str) -> str:
    import mlflow
    from mlflow.models.resources import DatabricksFunction, DatabricksServingEndpoint
    from quote_ai.llm import endpoints
    mlflow.set_registry_uri("databricks-uc")
    mlflow.set_experiment(experiment)
    resources = [DatabricksServingEndpoint(endpoint_name=endpoints.CHAT)] + \
        [DatabricksFunction(function_name=f"{names['ai']}.{f}") for f in TOOL_FUNCTIONS]
    with mlflow.start_run(run_name="quote_agent"):
        info = mlflow.pyfunc.log_model(name="quote_agent", python_model=str(SRC / "quote_ai" / "agents" / "agent.py"),
                                       code_paths=[str(SRC / "quote_ai")], resources=resources,
                                       pip_requirements=["mlflow>=3.1", "pydantic>=2.7", "pyyaml>=6", "requests",
                                                         "databricks-sdk", "openai"])
    version = mlflow.register_model(info.model_uri, f"{names['ai']}.quote_agent")
    return f"{names['ai']}.quote_agent version {version.version}"


def main(argv=None) -> None:
    a = parse(argv if argv is not None else sys.argv[1:])
    names = sql_files.names_for(a.catalog, dict(p.split("=", 1) for p in a.schemas.split(",") if p))
    if a.command == "register":
        print("registered", register(names, a.experiment))
        return
    from pyspark.sql import SparkSession
    from quote_ai.jobs.search_job import publish_wheel, run_sql_file
    spark = SparkSession.builder.getOrCreate()
    if a.command == "functions":
        if not (a.source_root and a.packages):
            raise SystemExit("functions needs --source-root and --packages")
        wheel = publish_wheel(a.source_root, a.packages)
        files = ["check_eligibility.sql", "check_fulfilment.sql", "tools.sql"] + (["gudid_live_lookup.sql"]
                                                                                  if a.live else [])
        for f in files:
            run_sql_file(spark, SRC / "quote_ai" / "rules" / "sql" / f, names, replacements(wheel, a.win_endpoint))
        print("tools use", wheel)
    elif a.command == "grants":
        for stmt in grants(names, a.principal, a.group, a.live):
            spark.sql(stmt)


if __name__ == "__main__":
    main()
