"""Rule-based routing (AI-21): each state routes to its specialist by rule, an action straight to its handler, and
only a question is routed by a model. Questions never change the quote's state."""
import pytest

import lakeside_fixtures as fx
from agent_fixtures import confirmed, priced, service, user
from quote_ai.agents import runtime
from quote_ai.agents.orchestrator import ROUTES, Orchestrator, Turn
from quote_ai.agents.standin import StandInClient
from quote_ai.domain.models import QuoteState as S


def orch(svc):
    return Orchestrator(runtime.demo_deps(), svc, StandInClient(), "standin",
                        groups=lambda u: {"DBxLab_Sales_Managers"} if u.startswith("mgr.") else set(),
                        inputs_as_of=lambda q: fx.fresh_inputs())


@pytest.mark.parametrize("state, route", sorted(ROUTES.items()))
def test_each_state_routes_to_its_specialist_by_rule(state, route):
    svc, q = confirmed()
    q = q.model_copy(update={"state": S(state)})
    assert orch(svc).route(Turn(text="next"), q) == (route, "rule")


def test_an_action_goes_straight_to_its_handler_and_questions_to_the_model():
    svc, q = priced()
    o = orch(svc)
    assert o.route(Turn(action="submit"), q) == ("submit", "action")
    assert o.route(Turn(text="Why is Glove C more expensive than Glove D?"), q) == ("question", "model")
    with pytest.raises(ValueError):
        o.route(Turn(action="approve_everything"), q)


@pytest.mark.parametrize("text, route", [("Why is Glove C more expensive than Glove D?", "knowledge"),
                                         ("What did Lakeside pay last quarter?", "analyst"),
                                         ("Is Glove D in stock in size XL?", "catalog")])
def test_questions_are_routed_and_never_change_the_state(text, route):
    svc, q = priced()
    events = list(orch(svc).handle(Turn(text=text), q, "rep.alice"))
    plan = events[0]
    assert (plan["type"], plan["route"], plan["by"]) == ("plan", route, "model")
    after = svc.repo.get(q.quote_id)
    assert (after.state, after.version, after.revision) == (q.state, q.version, q.revision)
    assert events[-1]["type"] == "done" and not events[-1]["violations"]


def test_shortlisted_goes_to_pricing_by_state_not_by_a_model():
    from test_quote_service import checks
    svc, q = confirmed()
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))
    client = StandInClient()
    o = Orchestrator(runtime.demo_deps(), svc, client, "standin")
    events = list(o.handle(Turn(text="next step"), q, "rep.alice"))
    assert events[0] == {"type": "plan", "route": "pricing", "by": "rule", "state": "SHORTLISTED", "version": 3}
    assert client.requests == []                                   # no model call at all
    answer = events[-1]["answer"]
    assert "GLOVE-A: $8.60 a box" in answer and "approval MANAGER" in answer and "Win chance" in answer


def test_actions_commit_through_quote_service():
    svc, q = priced(asked=8.00)
    o = orch(svc)
    events = list(o.handle(Turn(action="submit", args={"key": "k1"}), q, "rep.alice"))
    assert events[1]["state"] == "PENDING_APPROVAL"
    q = svc.repo.get(q.quote_id)
    events = list(o.handle(Turn(action="approve", args={"key": "k2", "version": q.version}), q, "mgr.maria"))
    assert events[1]["state"] == "APPROVED"
    q = svc.repo.get(q.quote_id)
    list(o.handle(Turn(action="issue", args={"key": "k3"}), q, "rep.alice"))
    assert [e.action for e in svc.repo.events(q.quote_id)][-3:] == ["submit", "approve", "issue"]


def test_the_chat_door_has_the_agent_shape():
    svc, q = confirmed()
    events = list(orch(svc).events([user("Find the options for this quote")], q, "rep.alice"))
    assert events[0]["route"] == "catalog" and events[-1]["type"] == "done"
    assert "Options in order" in events[-1]["answer"]
