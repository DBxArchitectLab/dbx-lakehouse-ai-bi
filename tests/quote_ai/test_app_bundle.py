"""The app as a bundle resource (AI-07), its app.yaml, and the bundle files QuoteAI added in Season 1."""
import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
RES = ROOT / "resources" / "quote_ai"
APP_DIR = ROOT / "src" / "quote_ai" / "app"


def _res(name):
    return yaml.safe_load((RES / name).read_text())["resources"]


def test_the_app_resource_least_privilege_and_reps_only():
    res = _res("quote_ai_app.app.yml")
    app = res["apps"]["quote_ai_app"]
    assert app["name"] == "quote-ai-${bundle.target}"
    assert (ROOT / "resources" / "quote_ai" / app["source_code_path"]).resolve() == APP_DIR
    assert app["permissions"] == [{"group_name": "${var.quote_ai_reps_group}", "level": "CAN_USE"},
                                  {"group_name": "${var.quote_ai_managers_group}", "level": "CAN_USE"}]   # AI-21
    by_name = {r["name"]: r for r in app["resources"]}
    assert by_name["sql-warehouse"]["sql_warehouse"]["permission"] == "CAN_USE"
    ext = by_name["extract-service"]["uc_securable"]
    assert ext["securable_type"] == "MODEL_SERVICE" and ext["permission"] == "EXECUTE"
    assert "quote_ai_extract" in ext["securable_full_name"]
    assert set(res) == {"apps"}                                      # one app per .app.yml file
    grants = _res("quote_ai_app_grants.job.yml")["jobs"]["quote_ai_app_grants"]
    params = grants["tasks"][0]["spark_python_task"]["parameters"]
    assert "${resources.apps.quote_ai_app.service_principal_client_id}" in params


def test_app_yaml_starts_uvicorn_and_takes_every_value_from_an_app_resource():
    spec = yaml.safe_load((APP_DIR / "app.yaml").read_text())
    assert spec["command"][:2] == ["uvicorn", "backend.main:app"]
    names = {r["name"] for r in _res("quote_ai_app.app.yml")["apps"]["quote_ai_app"]["resources"]}
    assert {e["valueFrom"] for e in spec["env"] if "valueFrom" in e} <= names
    assert not any("quote_ai_" in str(e.get("value", "")) for e in spec["env"])     # no schema name in the app


def test_every_quote_ai_job_and_pipeline_is_tagged():
    for f in RES.glob("quote_ai_*.yml"):
        res = yaml.safe_load(f.read_text()).get("resources", {})
        for key, job in (res.get("jobs") or {}).items():
            assert job.get("tags", {}).get("app") == "quote_ai", f"{f.name}: {key}"


def test_every_variable_quote_ai_uses_is_declared():
    declared = set(yaml.safe_load((ROOT / "databricks.yml").read_text())["variables"])
    used = {m for f in RES.glob("quote_ai_*.yml") for m in re.findall(r"\$\{var\.(\w+)\}", f.read_text())}
    assert used <= declared, used - declared


def test_every_schema_a_quote_ai_resource_references_exists():
    declared = set()
    for f in (ROOT / "resources").rglob("*.yml"):
        declared |= set((yaml.safe_load(f.read_text()) or {}).get("resources", {}).get("schemas", {}) or {})
    used = {m for f in RES.glob("quote_ai_*.yml") for m in re.findall(r"resources\.schemas\.(\w+)\.", f.read_text())}
    assert used <= declared, used - declared


def test_the_app_grants_are_least_privilege():
    spec = importlib.util.spec_from_file_location("product_job", ROOT / "src/quote_ai/jobs/product_job.py")
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    from quote_ai.storage.sql_files import names_for
    grants = job.app_grants(names_for("cat", {}), "1111-2222")
    gives = [g for g in grants if g.startswith("GRANT")]
    # AI-24: SELECT on the Delta copy of the quotes (live on Lakebase since AI-20), never MODIFY
    assert "GRANT SELECT ON TABLE cat.quote_ai_sales.quote TO `1111-2222`" in gives
    assert "REVOKE MODIFY ON TABLE cat.quote_ai_sales.quote FROM `1111-2222`" in grants
    assert not any("MODIFY" in g and "quote_ai_sales" in g for g in gives)
    assert not any("MODIFY" in g and "quote_ai_product" in g for g in gives)
    assert not any("bronze" in g for g in grants)
    # the gaps AI-24 closes: the app no longer reads cost, margin or every account's sales
    for t in ("price_book", "quote_fact", "order_line", "order_metrics", "quote_metrics"):
        assert not any(t in g for g in gives), t
        assert f"REVOKE SELECT ON TABLE cat.quote_ai_sales.{t} FROM `1111-2222`" in grants
    assert "GRANT SELECT, MODIFY ON TABLE cat.quote_ai_eval.candidate_cases TO `1111-2222`" in gives


def test_season5_bundle_files():
    """AI-18 to AI-21: the agent registration job, the agent app on the Agent Runtime, Lakebase for the QuoteAI app."""
    reg = _res("quote_ai_agent_registration.job.yml")["jobs"]["quote_ai_agent_registration"]
    assert [t["task_key"] for t in reg["tasks"]] == ["functions", "register", "grants"]
    agent = _res("quote_ai_agent.app.yml")["apps"]["quote_ai_agent"]
    assert agent["name"] == "quote-ai-agent-${bundle.target}"
    assert (RES / agent["source_code_path"]).resolve() == ROOT / "src" / "quote_ai" / "agent_app"
    assert agent["permissions"] == [{"service_principal_name":
                                     "${resources.apps.quote_ai_app.service_principal_client_id}", "level": "CAN_USE"}]
    spec = yaml.safe_load((ROOT / "src/quote_ai/agent_app/app.yaml").read_text())
    assert {e["valueFrom"] for e in spec["env"] if "valueFrom" in e} <= {r["name"] for r in agent["resources"]}
    app = {r["name"]: r for r in _res("quote_ai_app.app.yml")["apps"]["quote_ai_app"]["resources"]}
    assert app["lakebase"]["database"] == {"instance_name": "${var.quote_ai_lakebase_instance}",
                                           "database_name": "quote_ai", "permission": "CAN_CONNECT_AND_CREATE"}
    lake = _res("quote_ai_lakebase.database.yml")
    assert set(lake) == {"database_catalogs", "jobs"} and "quote_ai_lakebase" in lake["database_catalogs"]
