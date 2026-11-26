"""The DBxLab sales analyst as code (AI-11): every column it uses has a comment, the benchmark is complete and only
asks about the agent's tables, the agent's JSON is built from the YAML, and answers are read from a Genie message.
No workspace."""
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml

from quote_ai.semantic.genie import client, deploy_genie

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "quote_ai"
RAW = yaml.safe_load((SRC / "semantic" / "genie" / "sales_analyst.yml").read_text(encoding="utf-8"))
CASES = [json.loads(l) for l in (ROOT / "evaluation/quote_ai/v1/genie/cases.jsonl").read_text(encoding="utf-8")
         .splitlines() if l.strip()]
sys.path.insert(0, str(ROOT / "evaluation" / "quote_ai"))
import run_eval  # noqa: E402


def commented_columns() -> dict[str, set[str]]:
    """Table (as written, {sales}.quote_fact) -> its columns that carry a comment, from every SQL file in src/."""
    out: dict[str, set[str]] = {}
    for f in SRC.rglob("*.sql"):
        text = f.read_text(encoding="utf-8")
        for table, body in re.findall(r"CREATE TABLE IF NOT EXISTS (\{\w+\}\.\w+) \((.*?)\n\) COMMENT", text, re.S):
            out.setdefault(table, set()).update(
                m.group(1) for m in re.finditer(r"^\s*(\w+)\s+[A-Z].*\bCOMMENT '", body, re.M))
        for table, col in re.findall(r"COMMENT ON COLUMN (\{\w+\}\.\w+)\.(\w+) IS '", text):
            out.setdefault(table, set()).add(col)
        for view, spec in re.findall(r"CREATE OR REPLACE VIEW (\{\w+\}\.\w+)\s+WITH METRICS.*?\$\$(.*?)\$\$", text,
                                     re.S):
            y = yaml.safe_load(re.sub(r"\{(\w+)\}", r"\1", spec))
            out.setdefault(view, set()).update(x["name"] for x in y["dimensions"] + y["measures"]
                                               if x.get("comment"))
    return out


def test_every_column_the_agent_uses_has_a_comment():
    have = commented_columns()
    for t in RAW["tables"]:
        missing = set(t["columns"]) - have.get(t["name"], set())
        assert not missing, f"{t['name']}: no comment on {sorted(missing)} (src/quote_ai/semantic/comments.sql)"


def test_every_table_the_agent_uses_has_a_table_comment():
    text = "\n".join(f.read_text(encoding="utf-8") for f in SRC.rglob("*.sql"))
    for t in RAW["tables"]:
        name = re.escape(t["name"])
        assert re.search(rf"(CREATE (TABLE IF NOT EXISTS|OR REPLACE VIEW) {name}\b.*?COMMENT '|COMMENT ON TABLE "
                         rf"{name} IS)", text, re.S) or t["name"].endswith("device_size"), t["name"]


def test_the_benchmark_has_40_cases_25_sales_15_catalog_with_g07():
    assert len(CASES) == 40 and len({c["id"] for c in CASES}) == 40
    assert sum(c["section"] == "sales" for c in CASES) == 25
    assert sum(c["section"] == "catalog" for c in CASES) == 15
    g07 = next(c for c in CASES if c["id"] == "g-07")
    assert "MEASURE(win_rate)" in g07["expected_sql"] and g07["compare"] == "result"


def test_the_expected_sql_uses_only_the_agents_tables_and_metric_views_for_ratios():
    tables = {t["name"] for t in RAW["tables"]}
    for c in CASES + [{"id": e["question"], "expected_sql": e["sql"]} for e in RAW["example_queries"]]:
        used = set(re.findall(r"\{\w+\}\.\w+", c["expected_sql"]))
        assert used and used <= tables, (c["id"], used - tables)
        if "win_rate" in c["expected_sql"] or "approval_rate" in c["expected_sql"]:
            assert "MEASURE(" in c["expected_sql"], c["id"]
        for alias_col in re.findall(r"\b[a-z]\.(\w+)", c["expected_sql"]):
            assert any(alias_col in t["columns"] for t in RAW["tables"]), (c["id"], alias_col)


def test_the_spec_renders_for_a_dev_copy_and_the_agent_json_carries_everything():
    spec = deploy_genie.load_spec("cat", {"quote_ai_sales": "dev_me_quote_ai_sales"})
    cases = deploy_genie.load_cases("cat", {"quote_ai_sales": "dev_me_quote_ai_sales"})
    assert spec["tables"][0]["name"] == "cat.dev_me_quote_ai_sales.quote_metrics"
    assert all("{" not in c["expected_sql"] for c in cases)
    space = json.loads(deploy_genie.serialized_space(spec, cases))
    assert len(space["data_sources"]["tables"]) == len(RAW["tables"])
    assert len(space["benchmarks"]["questions"]) == 40
    assert any("Lakeside" in e["question"][0] for e in space["instructions"]["example_question_sqls"])
    text = "".join(space["instructions"]["text_instructions"][0]["content"])
    assert "orders" in text and "box" in text.lower() and "recommend" in text
    again = json.loads(deploy_genie.serialized_space(spec, cases))
    assert again == space                                   # stable IDs: a redeploy updates, never duplicates


def test_deploy_updates_the_agent_with_the_same_title():
    calls = {}
    existing = SimpleNamespace(space_id="s-1", title="DBxLab sales analyst")
    genie = SimpleNamespace(list_spaces=lambda: SimpleNamespace(spaces=[existing]),
                            create_space=lambda **kw: calls.setdefault("create", kw),
                            update_space=lambda sid, **kw: calls.setdefault("update", (sid, kw)) and existing)
    fake = SimpleNamespace(genie=genie)
    assert deploy_genie.deploy("cat", "wh", client=fake) == "s-1"
    assert "create" not in calls and calls["update"][0] == "s-1" and calls["update"][1]["warehouse_id"] == "wh"


def test_answer_from_a_genie_message():
    q = SimpleNamespace(query="SELECT 1", description="Lakeside orders")
    msg = SimpleNamespace(conversation_id="c", message_id="m", status=SimpleNamespace(value="COMPLETED"),
                          attachments=[SimpleNamespace(text=SimpleNamespace(content="Lakeside bought 3 SKUs."),
                                                       query=None, attachment_id="a0"),
                                       SimpleNamespace(text=None, query=q, attachment_id="a1")])
    a = client.answer_from(msg, lambda att: (["sku_id", "boxes"], [["GLOVE-A-M", "200"]]) if att == "a1" else None)
    assert a["text"] == "Lakeside bought 3 SKUs." and a["sql"] == "SELECT 1" and a["status"] == "COMPLETED"
    assert a["columns"] == ["sku_id", "boxes"] and a["rows"] == [["GLOVE-A-M", "200"]]


def test_score_genie_compares_results_not_sql_text_and_keeps_the_failed_sql():
    results = {"EXPECTED-1": [{"region": "Midwest", "w": 0.4}], "EXPECTED-2": [(10,)],
               "GENIE-1": [{"r": "midwest", "x": 0.40000001}], "GENIE-2": [(12,)]}
    cases = [{"id": "g-07", "question": "win rate", "expected_sql": "EXPECTED-1"},
             {"id": "g-10", "question": "what did Lakeside buy", "expected_sql": "EXPECTED-2"}]
    answers = {"win rate": {"sql": "GENIE-1"}, "what did Lakeside buy": {"sql": "GENIE-2"}}
    r = run_eval.score_genie(cases, lambda q: answers[q], lambda sql: results[sql])
    assert r["score"] == 0.5 and r["failures"][0]["id"] == "g-10" and r["failures"][0]["genie_sql"] == "GENIE-2"
    assert run_eval.same_result([(1,), (2,)], [(2,), (1,)]) and not run_eval.same_result([(1,), (2,)], [(2,), (1,)],
                                                                                          ordered=True)
