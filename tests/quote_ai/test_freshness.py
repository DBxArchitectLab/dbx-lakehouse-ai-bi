"""The freshness policy (AI-20): a copy informs, it never issues. Stale inputs block ISSUED and the refusal names
them with their dates; the GUDID fallback is labelled with its source and as-of date."""
from datetime import timedelta

import pytest

import lakeside_fixtures as fx
from agent_fixtures import NO_WAIT, agent, confirmed, priced, toolbox, user
from quote_ai.rules.freshness import stale_details
from quote_ai.rules.policy import Policy
from quote_ai.services.quote_service import StaleEvidence


def approved(asked=8.40):
    svc, q = priced(asked=asked)
    return svc, svc.submit(q.quote_id, q.revision, "rep.alice", key="s")      # within 5%: approved by rule


def test_three_day_old_gudid_evidence_blocks_issued_and_is_named():
    svc, q = approved()
    old = fx.fresh_inputs() | {"gudid": fx.NOW - timedelta(days=3)}
    with pytest.raises(StaleEvidence, match="gudid as of 2026-11-11 09:00 UTC") as e:
        svc.issue(q.quote_id, q.revision, "rep.alice", key="i1", inputs_as_of=old)
    assert e.value.stale == ["gudid as of 2026-11-11 09:00 UTC"]
    assert svc.repo.get(q.quote_id).state.value == "APPROVED"            # nothing written


def test_fresh_inputs_issue():
    svc, q = approved()
    q = svc.issue(q.quote_id, q.revision, "rep.alice", key="i1", inputs_as_of=fx.fresh_inputs())
    assert q.state.value == "ISSUED"
    assert svc.repo.events(q.quote_id)[-1].detail["inputs_as_of"]["gudid"].startswith("2026-11-14T07")


def test_the_policy_limits_are_48_hours_for_gudid_and_24_for_stock():
    p = Policy().freshness
    edge = fx.fresh_inputs() | {"gudid": fx.NOW - timedelta(hours=47), "inventory": fx.NOW - timedelta(hours=25)}
    assert stale_details(edge, fx.NOW, p) == ["inventory as of 2026-11-13 08:00 UTC"]


def test_gudid_down_the_answer_comes_from_the_copy_with_its_date():
    """AI-20's passing case, first half: the live API is down, get_device falls back and says so."""
    _, q = confirmed()
    box = toolbox(q, live_first=True)
    box.deps.live_gudid.fetch.down = True
    d = box.run("get_device", {"product_id": "GLOVE-A"})
    assert d["source"] == "lakehouse_copy" and d["as_of"] == "2026-11-12" and "live GUDID unavailable" in d["fallback"]
    box.deps.live_gudid.fetch.down = False
    live = box.run("get_device", {"product_id": "GLOVE-A"})
    assert live["source"] == "accessgudid_live" and live["public_version_number"] == 3


def test_is_glove_a_current_with_the_api_down_is_answered_from_the_copy():
    _, q = confirmed()
    a = agent(live_first=True)
    a.deps.live_gudid.fetch.down = True
    events = list(a.events([user("Is Glove A current?")], q, "rep.alice", retry=NO_WAIT))
    answer = events[-1]["answer"]
    assert "lakehouse copy as of 2026-11-12" in answer and "live GUDID unavailable" in answer
    sources = [e["source"] for e in events if e["type"] == "tool_result"]
    assert sources and set(sources) == {"lakehouse_copy"}
