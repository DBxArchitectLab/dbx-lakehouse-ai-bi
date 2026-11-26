"""AI security (AI-24): the back end queries as the signed-in rep, the row filter and the cost mask (in SQL and their
Python twin), per-tool grants, the app's identity cut down, and the Data authorization suite with its two breaks."""
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / "src" / "quote_ai" / "rules" / "sql"
sys.path.insert(0, str(ROOT / "evaluation" / "quote_ai"))

from quote_ai.rules import access  # noqa: E402
from quote_ai.rules.access import Identity  # noqa: E402
from quote_ai.storage.sql_files import names_for  # noqa: E402

OWNERS = {"rep.alice": ["ACC-001", "ACC-004"], "rep.bob": ["ACC-002", "ACC-003"]}
ALICE = Identity("rep.alice@dbxlab.example", frozenset({access.REPS_GROUP}))
BOB = Identity("rep.bob@dbxlab.example", frozenset({access.REPS_GROUP}))
MARIA = Identity("mgr.maria@dbxlab.example", frozenset({access.MANAGERS_GROUP}))
APP = Identity("quote-ai-app-sp", kind="app")


# ------------------------------------------------------------------ the rules (Python twin of the SQL)

def test_row_filter_managers_all_reps_their_own():
    rows = [{"account_id": a, "unit_price": 8.0} for a in ("ACC-001", "ACC-002", "ACC-003")]
    assert [r["account_id"] for r in access.filter_rows(rows, ALICE, OWNERS)] == ["ACC-001"]
    assert [r["account_id"] for r in access.filter_rows(rows, BOB, OWNERS)] == ["ACC-002", "ACC-003"]
    assert len(access.filter_rows(rows, MARIA, OWNERS)) == 3
    assert len(access.filter_rows(rows, APP, OWNERS)) == 3          # why a rep's data never runs as the app
    assert access.owned_accounts(Identity("new.rep@dbxlab.example"), OWNERS) == set()


def test_cost_mask():
    row = {"account": "Lakeside", "avg_price": 8.21, "avg_cost": 6.5, "margin_pct": 20.8}
    assert access.mask_row(row, ALICE) == {"account": "Lakeside", "avg_price": 8.21, "avg_cost": None,
                                          "margin_pct": None}
    assert access.mask_row(row, MARIA) == row


def test_the_sql_says_what_the_python_does():
    filters = (SQL / "row_filters.sql").read_text(encoding="utf-8")
    masks = (SQL / "column_masks.sql").read_text(encoding="utf-8")
    for text in (filters, masks):
        assert f"is_account_group_member('{access.MANAGERS_GROUP}')" in text
    assert "SET ROW FILTER {sales}.account_filter ON (account_id)" in filters
    assert {t for t in re.findall(r"ALTER TABLE \{sales\}\.(\w+) SET ROW FILTER", filters)} == \
        {"quote_fact", "order_line", "rep_account"}
    assert re.findall(r"ALTER TABLE \{sales\}\.(\w+) ALTER COLUMN unit_cost SET MASK", masks) == \
        ["quote_fact", "order_line"]
    view = (ROOT / "src" / "quote_ai" / "product" / "sku_model.sql").read_text(encoding="utf-8")
    block = view[view.index("CREATE OR REPLACE VIEW {sales}.price_book"):view.index("FROM {sales_bronze}.price_book_raw")]
    assert "is_account_group_member('DBxLab_Sales_Managers') THEN CAST(cost" in block and "floor_price" in block


def test_the_security_job_renders_every_statement():
    import importlib.util
    spec = importlib.util.spec_from_file_location("security_job", ROOT / "src/quote_ai/jobs/security_job.py")
    job = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(job)
    names = names_for("cat", {"quote_ai_sales": "dev_me_quote_ai_sales"})
    f = job.filter_statements(names)
    assert any("CREATE OR REPLACE FUNCTION cat.dev_me_quote_ai_sales.account_filter" in s for s in f)
    assert len(job.mask_statements(names)) == 3
    g = job.grant_statements(names, "DBxLab_Sales_Reps", "DBxLab_Sales_Managers", {"pricing": "sp-pricing"})
    assert "GRANT SELECT ON TABLE cat.dev_me_quote_ai_sales.order_metrics TO `DBxLab_Sales_Reps`" in g
    assert "GRANT EXECUTE ON FUNCTION cat.quote_ai_ai.price_quote TO `sp-pricing`" in g
    assert not any("{" in s for s in f + g)
    with pytest.raises(SystemExit):
        job.grant_statements(names, "reps; DROP", "m", {})
    assert "system.query.history" in job.audit_sql("quote-ai-dev")


# ------------------------------------------------------------------ per-tool grants (sec-05)

def test_the_tool_grants_are_least_privilege():
    from quote_ai.agents import grants
    from quote_ai.agents.specialists import knowledge
    assert grants.findings() == []
    assert "price_quote" not in knowledge.TOOLS
    g = grants.load()
    g["specialists"]["knowledge"] = g["specialists"]["knowledge"] + ["price_quote"]     # the demo's break
    assert grants.findings(g) == ["knowledge holds price_quote, which only pricing may run",
                                  "knowledge is granted price_quote, which its code does not use"]


# ------------------------------------------------------------------ the back end as the rep

def test_the_forwarded_token_reaches_the_identity_but_never_a_response():
    pytest.importorskip("fastapi")
    from quote_ai.app.backend.auth import User
    from quote_ai.app.backend.deps import identity
    u = User(id="rep.alice@dbxlab.example", token="secret-token")
    assert "secret" not in repr(u) and "token" not in u.model_dump()
    ctx = SimpleNamespace(groups=lambda user: {access.REPS_GROUP})
    i = identity(ctx, u)
    assert i.token == "secret-token" and not i.is_manager and "secret" not in repr(i)


def test_the_workspace_store_refuses_to_run_without_the_reps_token():
    from quote_ai.storage.user_data import SqlUserData
    with pytest.raises(PermissionError):
        SqlUserData(ALICE, "wh", names_for("cat", {}), None)


def test_demo_user_data_mirrors_the_rules():
    from quote_ai.storage.user_data import DemoUserData
    assert set(DemoUserData(BOB).accounts()) == {"ACC-002", "ACC-003"}
    assert len(DemoUserData(MARIA).accounts()) == 4
    rep_book, mgr_book = DemoUserData(ALICE).price_book(), DemoUserData(MARIA).price_book()
    assert all(r.cost is None and r.floor_price == 7.28 for r in rep_book)
    assert all(r.cost == 6.50 for r in mgr_book)
    rows = DemoUserData(BOB).ask_data("What does Lakeside pay per box?")["rows"]
    assert rows == []


# ------------------------------------------------------------------ the suite and its breaks

def test_data_authorization_is_green_and_its_breaks_are_red():
    pytest.importorskip("fastapi")
    import security
    ok = security.run()
    assert ok["passed"] and ok["unauthorized"] == 0 and ok["cases"] == 5 and ok["wrong_denials"] == 0
    gap = security.run("app_identity")                  # the before picture: Genie and routes as the app
    assert gap["unauthorized"] == 2 and {p["case_id"] for p in gap["per_case"] if p["unauthorized"]} == \
        {"sec-03", "sec-04"}
    grant = security.run("grant_price_quote_to_knowledge")
    assert [p["case_id"] for p in grant["per_case"] if p["unauthorized"]] == ["sec-05"]
