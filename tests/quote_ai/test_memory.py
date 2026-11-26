"""The agent's memory on Lakebase (AI-20): isolation per rep, confirm before save, memory informs and never replaces
a requirement, retention."""
from datetime import timedelta

import pytest

import lakeside_fixtures as fx
from agent_fixtures import agent, confirmed, service, user
from quote_ai.agents import memory as m
from quote_ai.storage.lakebase import sqlite_db
from quote_ai.storage.lakebase_repository import LakebaseQuoteRepository
from quote_ai.storage.repository import QuoteNotFound


@pytest.fixture
def lake():
    db = sqlite_db()
    svc = service(LakebaseQuoteRepository(db))
    svc.memory = m.Memory(db, clock=lambda: fx.NOW)
    return svc, svc.memory


def test_rep_north_cannot_load_rep_souths_quote(lake):
    svc, mem = lake
    _, q = confirmed(svc)                                   # rep.alice (rep_south) owns the Lakeside quote
    assert m.load_context(mem.db, "rep.alice", q.quote_id)["quote"]["state"] == "REQUIREMENTS_CONFIRMED"
    with pytest.raises(QuoteNotFound):
        m.load_context(mem.db, "rep.bob", q.quote_id)
    mem.append("rep.alice", q.quote_id, "user", "Can you do size XL in the second option?")
    assert mem.messages("rep.bob", q.quote_id) == []


def test_memory_is_written_only_with_confirmed_by(lake):
    svc, mem = lake
    with pytest.raises(m.MemoryRefused):
        mem.save_fact("Lakeside Regional Clinic", "latex_free", "true", owner_rep="rep.alice", confirmed_by=None)
    assert mem.facts("Lakeside Regional Clinic", "rep.alice") == []
    saved = svc.confirm_fact("Lakeside Regional Clinic", "latex_free", "true", "rep.alice")
    assert saved["confirmed_by"] == "rep.alice"
    row, = mem.facts("Lakeside Regional Clinic", "rep.alice")
    assert (row["fact_key"], row["fact_value"], row["confirmed_by"]) == ("latex_free", "true", "rep.alice")


def test_the_table_itself_refuses_a_fact_without_confirmed_by(lake):
    _, mem = lake
    with pytest.raises(mem.db.integrity):
        with mem.db.tx() as tx:
            tx.execute("INSERT INTO customer_memory (account_id, fact_key, fact_value, owner_rep, confirmed_at) "
                       "VALUES (%s, %s, %s, %s, %s)", ("ACC-001", "latex_free", "true", "rep.alice", fx.NOW))


def test_another_reps_facts_stay_theirs_unless_shared(lake):
    svc, mem = lake
    svc.confirm_fact("Lakeside Regional Clinic", "latex_free", "true", "rep.alice")
    assert mem.facts("Lakeside Regional Clinic", "rep.bob") == []
    with pytest.raises(m.MemoryRefused):
        mem.save_fact("Lakeside Regional Clinic", "latex_free", "false", owner_rep="rep.bob", confirmed_by="rep.bob")
    svc.confirm_fact("Harbor Point Dental Group", "powder_free", "true", "rep.bob", shared=True)
    assert [f["fact_key"] for f in mem.facts("Harbor Point Dental Group", "rep.alice")] == ["powder_free"]


def test_memory_informs_and_the_rep_confirms_it_on_the_quote(lake):
    svc, mem = lake
    svc.confirm_fact("Lakeside Regional Clinic", "latex_free", "true", "rep.alice")
    r1 = fx.lakeside_r1().model_copy(update={"latex_free": None})        # next week: the request doesn't say it
    q = svc.create("Lakeside Regional Clinic", "rep.alice", "300 boxes of nitrile exam gloves", r1, quote_id="Q-NEXT")
    assert mem.suggested_requirements(q.account, "rep.alice", q.requirements) == {"latex_free": True}
    assert q.requirements.latex_free is None                              # a suggestion changes nothing
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100,
                    from_memory={"latex_free": True})
    assert q.requirements.latex_free is True
    assert svc.repo.events(q.quote_id)[-1].detail["from_memory"] == {"latex_free": True}


def test_conversations_resume_and_expire_after_30_days(lake):
    svc, mem = lake
    _, q = confirmed(svc)
    mem.append("rep.alice", q.quote_id, "user", "Find the options")
    mem.append("rep.alice", q.quote_id, "assistant", "Options in order: 1. GLOVE-A, 2. GLOVE-D, 3. GLOVE-C")
    assert [x["role"] for x in mem.load_context("rep.alice", q.quote_id)["messages"]] == ["user", "assistant"]
    later = m.Memory(mem.db, clock=lambda: fx.NOW + timedelta(days=31))
    assert later.purge() == 2 and later.messages("rep.alice", q.quote_id) == []


def test_the_next_morning_the_second_option_is_known():
    """The conversation outlives the session: the agent knows which glove the second option was."""
    _, q = confirmed()
    a = agent()
    first = list(a.events([user("Find the options for the Lakeside quote and price them.")], q, "rep.alice"))
    saved = [user("Find the options for the Lakeside quote and price them."),
             {"role": "assistant", "content": first[-1]["answer"]}]
    events = list(a.events(saved + [user("Can you do size XL in the second option?")], q, "rep.alice"))
    steps = [(e["name"], e["args"]) for e in events if e["type"] == "step"]
    assert steps == [("check_fulfilment", {"product_ids": ["GLOVE-D"]}), ("price_quote", {"product_id": "GLOVE-D"})]
    assert "Glove D: $8.60 a box" in events[-1]["answer"]


def test_the_agent_proposes_a_fact_and_writes_nothing():
    _, q = confirmed()
    events = list(agent().events([user("Lakeside said they are always latex-free; save that.")], q, "rep.alice"))
    proposal, = [e["proposal"] for e in events if e["type"] == "memory_proposal"]
    assert proposal["fact_key"] == "latex_free" and "Save for Lakeside Regional Clinic" in proposal["question"]
