"""The agent's tools, each alone (AI-18, AI-19): fixed inputs, fixed outputs, before the agent ever calls them."""
import json
import re
from pathlib import Path

import pytest

import lakeside_fixtures as fx
from agent_fixtures import confirmed, priced, toolbox
from quote_ai.agents import tools
from quote_ai.rules import uc
from quote_ai.storage import sql_files

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def box():
    _, q = confirmed()
    return toolbox(q)


def test_the_specs_say_what_each_tool_is_for():
    names = [s["function"]["name"] for s in tools.TOOL_SPECS]
    assert tools.AI18_TOOLS == names[:8] and len(names) == len(set(names))
    for s in tools.TOOL_SPECS:
        assert len(s["function"]["description"]) >= 60, s["function"]["name"]
    d = tools.SPECS["check_eligibility"]["function"]["description"]
    assert "verified_match" in d and "Never treat unknown as eligible" in d
    assert "not a decision" in tools.SPECS["search_candidates"]["function"]["description"]
    assert "Writes nothing" in tools.SPECS["propose_transition"]["function"]["description"]


def test_no_tool_writes_a_quote():
    assert tools.WRITE_TOOLS == frozenset()


def test_search_candidates_finds_candidates_only(box):
    out = box.run("search_candidates", {})
    ids = [c["product_id"] for c in out["candidates"]]
    assert {"GLOVE-A", "GLOVE-C", "GLOVE-D"} <= set(ids) and "decides" in out["note"]


def test_check_eligibility_three_states(box):
    out = box.run("check_eligibility", {"product_ids": ["GLOVE-A", "GLOVE-B", "GLOVE-E"]})
    st = {r["product_id"]: r for r in out["results"]}
    assert st["GLOVE-A"]["status"] == "eligible"
    assert st["GLOVE-B"]["status"] == "needs_evidence" and st["GLOVE-B"]["checks"]["chemo_tested"] == "unknown"
    assert st["GLOVE-E"]["status"] == "excluded" and st["GLOVE-E"]["checks"]["latex_free"] == "verified_mismatch"
    assert out["source"] == "rules"


def test_get_evidence_tells_a_claim_from_a_fact(box):
    a = box.run("get_evidence", {"product_id": "GLOVE-A", "attribute": "chemo_tested"})
    b = box.run("get_evidence", {"product_id": "GLOVE-B", "attribute": "chemo_tested"})
    assert a["verified"] and not b["verified"] and b["rows"]
    assert {r["attribute_name"] for r in a["rows"]} == {"chemo_tested"}


def test_get_device_is_the_lakehouse_copy_with_its_date(box):
    d = box.run("get_device", {"product_id": "GLOVE-A"})
    assert d["source"] == "lakehouse_copy" and d["as_of"] == "2026-11-12" and d["labeled_no_nrl"] is True


def test_check_fulfilment_and_the_what_if(box):
    out = {r["product_id"]: r for r in box.run("check_fulfilment", {"product_ids": ["GLOVE-A", "GLOVE-C",
                                                                                   "GLOVE-D"]})["results"]}
    assert out["GLOVE-A"]["on_time"] and not out["GLOVE-C"]["on_time"]
    assert out["GLOVE-C"]["label"] == "alternative, needs acceptance"
    what_if = box.run("check_fulfilment", {"product_ids": ["GLOVE-A", "GLOVE-D"],
                                           "unavailable_skus": ["GLOVE-A-M"]})
    st = {r["product_id"]: r for r in what_if["results"]}
    assert not st["GLOVE-A"]["on_time"] and st["GLOVE-D"]["on_time"]
    assert what_if["assumed_out_of_stock"] == ["GLOVE-A-M"]


def test_price_quote_prices_the_four_lines_from_the_book(box):
    p = box.run("price_quote", {"product_id": "GLOVE-A"})
    assert [(l["size"], l["boxes"], l["unit_price"]) for l in p["lines"]] == [
        ("S", 60, 8.60), ("M", 140, 8.60), ("L", 140, 8.60), ("XL", 60, 8.60)]
    assert p["total"] == 3440.0 and p["price_book_ids"][0] == "PB-DEMO-GLOVE-A-S"
    band = box.run("price_quote", {"product_id": "GLOVE-A", "asked_unit_price": 8.00})["discount_band"]
    assert band["approval"] == "MANAGER" and band["discount_pct"] == 6.98


def test_a_rule_answer_is_returned_not_raised(box):
    out = box.run("price_quote", {"product_id": "GLOVE-F"})      # no price book row for Glove F
    assert "refused" in out and "no price book row" in out["reason"]


def test_win_probability_falls_as_the_price_rises(box):
    w = box.run("win_probability", {"product_id": "GLOVE-A", "prices": [8.60, 8.00]})
    chances = {c["price"]: c["win_chance"] for c in w["chances"]}
    assert chances[8.00] > chances[8.60] and w["source"] == "model"


def test_propose_transition_writes_nothing():
    svc, q = priced()
    box = toolbox(q)
    out = box.run("propose_transition", {"target": "ISSUED", "reason": "the rep asked"})
    assert out["proposal"]["target"] == "ISSUED" and out["proposal"]["from_state"] == "PRICED"
    again = box.run("propose_transition", {"target": "ISSUED", "reason": "asked again"})
    assert again["proposal"]["idempotency_key"] == out["proposal"]["idempotency_key"]     # one proposal (AI-19)
    after = svc.repo.get(q.quote_id)
    assert (after.state, after.version, after.revision) == (q.state, q.version, q.revision)


def test_unknown_tools_and_bad_arguments_are_refused(box):
    assert box.run("update_quote", {})["error"] == "UNKNOWN_TOOL"
    assert box.run("check_eligibility", {})["error"] == "BAD_ARGUMENTS"


def test_the_tools_as_unity_catalog_functions():
    text = (ROOT / "src/quote_ai/rules/sql/tools.sql").read_text(encoding="utf-8")
    for fn in ("search_candidates", "get_evidence", "get_device", "price_quote", "price_rules", "win_probability"):
        assert re.search(rf"CREATE OR REPLACE FUNCTION {{ai}}\.{fn}\(", text), fn
    stmts = sql_files.load(ROOT / "src/quote_ai/rules/sql/tools.sql", **sql_files.names_for("cat", {}))
    assert all("COMMENT '" in s for s in stmts if "CREATE" in s)
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("--"))
    assert not re.search(r"(?i)\b(INSERT|UPDATE|DELETE|MERGE)\b", code)


def test_price_json_is_the_same_rule_as_price_quote():
    book = [r.model_dump() for r in fx.price_book() if r.sku_id.startswith("GLOVE-A")]
    skus = [{"sku_id": f"GLOVE-A-{s}", "size": s, "unit": "box", "gloves_per_unit": 100} for s in fx.SIZES]
    lines = [{"size": s, "quantity": n, "unit": "box"} for s, n in fx.LAKESIDE_SPLIT.items()]
    out = json.loads(uc.price_json(json.dumps(book, default=str), json.dumps(skus), json.dumps(lines), "standard"))
    assert out["total"] == 3440.0 and out["lines"][1]["price_book_id"] == "PB-A-M"
    refused = json.loads(uc.price_json(json.dumps(book, default=str), json.dumps(skus), json.dumps(lines), "contract"))
    assert "no price book row" in refused["refused"]


def test_the_grants_are_execute_on_the_tools_only():
    from quote_ai.jobs import agent_job
    names = sql_files.names_for("cat", {})
    g = agent_job.grants(names, "sp-123", "DBxLab_Sales_Reps", live=True)
    assert all(s.startswith(("GRANT EXECUTE ON FUNCTION cat.quote_ai_ai.", "GRANT USE SCHEMA ON SCHEMA cat.quote_ai_ai",
                             "GRANT USE CONNECTION ON CONNECTION quote_ai_accessgudid TO `sp-123`")) for s in g)
    assert not [s for s in g if "quote_ai_sales" in s or "MODIFY" in s or "SELECT" in s]
    assert sum("USE CONNECTION" in s for s in g) == 1          # the agent's identity only, not the reps
