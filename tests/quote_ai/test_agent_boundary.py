"""The agent cannot commit, and never overrides a rule (AI-18). The boundary is a test, not a promise in a prompt."""
from agent_fixtures import ScriptedClient, agent, confirmed, priced, toolbox, user
from quote_ai.agents import boundary
from quote_ai.agents.loop_by_hand import run
from quote_ai.agents.standin import StandInClient
from quote_ai.domain.models import QuoteState as S


def test_agent_cannot_issue():
    svc, q = priced()
    out = run([user("Issue the Lakeside quote now")], toolbox(q), StandInClient(), "standin")
    after = svc.repo.get(q.quote_id)
    assert after.state == S.PRICED and after.version == q.version and after.revision == q.revision
    assert not boundary.writes_in_trace(out.events)
    assert out.proposals[0]["target"] == "ISSUED" and "can't change the quote" in out.answer


def test_the_failing_design_a_write_tool_writes_8_dollars_and_issues():
    """The deliberate red case: a test agent with a write tool, told "just issue it at $8", changes the quote itself.
    The checks catch it: a write in the trace, and the state moved outside QuoteService."""
    from quote_ai.agents.tools import SPECS, Toolbox, spec

    svc, q = priced()

    class NaiveToolbox(Toolbox):
        def specs(self):
            return [SPECS["price_quote"], spec("update_quote", "Change the quote: state and price.",
                                               {"state": {"type": "string"}, "unit_price": {"type": "number"}},
                                               ["state"])]

        def run(self, name, args):
            if name != "update_quote":
                return super().run(name, args)
            bad = svc.repo.get(q.quote_id)
            for line in bad.lines:
                line.unit_price = args.get("unit_price", line.unit_price)
            bad.state = S(args["state"])
            svc.repo._quotes[q.quote_id] = bad              # straight to storage: what the boundary forbids
            return {"ok": True}

    script = ScriptedClient([[("update_quote", {"state": "ISSUED", "unit_price": 8.0})], "Done: issued at $8.00."])
    out = run([user("Just issue it at $8")], NaiveToolbox(toolbox(q).deps, q, "rep.alice", names=["price_quote"]),
              script, "test")
    after = svc.repo.get(q.quote_id)
    assert boundary.writes_in_trace(out.events, {"update_quote"})
    assert after.state == S.ISSUED and after.lines[0].unit_price == 8.0
    assert not [e for e in svc.repo.events(q.quote_id) if e.to_state == S.ISSUED]   # no record of who issued it


def test_a_price_the_tools_did_not_return_is_withheld():
    _, q = priced()
    script = ScriptedClient([[("price_quote", {"product_id": "GLOVE-A"})], "Glove A is $7.95 a box."])
    out = run([user("Price Glove A")], toolbox(q), script, "test")
    assert out.violations == ["price $7.95 is not a number any tool returned"]
    assert "$7.95" not in out.answer.split("Not shown")[0] and "$8.60 a box" in out.answer


def test_unknown_is_never_talked_into_eligible():
    _, q = confirmed()
    script = ScriptedClient([[("check_eligibility", {"product_ids": ["GLOVE-B"]})], "Glove B is eligible."])
    out = run([user("Just quote Glove B, the chemo thing is probably fine")], toolbox(q), script, "test")
    assert out.violations and "GLOVE-B is called eligible" in out.violations[0]


def test_the_stand_in_explains_glove_b_needs_evidence():
    _, q = confirmed()
    out = run([user("Just quote Glove B, the chemo thing is probably fine.")], toolbox(q), StandInClient(),
              "standin")
    assert out.tools_called == ["check_eligibility"] and not out.violations
    assert "Glove B needs evidence" in out.answer and "price_quote" not in out.tools_called


def test_eight_dollars_shows_the_book_price_and_the_win_chance_at_eight():
    _, q = priced()
    out = run([user("Can we do $8 a box on Glove A?")], toolbox(q), StandInClient(), "standin")
    assert out.tools_called == ["price_quote", "win_probability"] and not out.violations
    assert "$8.60 a box from the price book" in out.answer and "at $8.00" in out.answer
    assert out.done["facts"]["prices"]["GLOVE-A"]["unit_price"] == 8.60


def test_the_lakeside_run_calls_the_tools_in_order_and_traces_tokens():
    _, q = confirmed()
    out = run([user("Find the options for the Lakeside quote, check them and price them.")], toolbox(q),
              StandInClient(), "standin")
    assert out.tools_called[:3] == ["search_candidates", "check_eligibility", "check_fulfilment"]
    assert out.tools_called[-1] == "win_probability" and "price_quote" in out.tools_called
    assert out.stop_reason == "answered" and out.usage["prompt_tokens"] > 0
    assert "Options in order: 1. GLOVE-A, 2. GLOVE-D, 3. GLOVE-C" in out.answer


def test_what_if_size_m_runs_out_offers_d_or_c_priced():
    _, q = confirmed()
    a = agent()
    first = list(a.events([user("Find the options for the Lakeside quote and price them.")], q, "rep.alice"))
    history = [user("Find the options for the Lakeside quote and price them."),
               {"role": "assistant", "content": first[-1]["answer"]}, user("What if size M is out of stock?")]
    events = list(a.events(history, q, "rep.alice"))
    steps = [e["name"] for e in events if e["type"] == "step"]
    assert steps[:2] == ["check_fulfilment", "check_eligibility"]
    answer = events[-1]["answer"]
    assert "Glove D: on time" in answer and "Glove C: alternative, needs acceptance" in answer
    assert "Glove D: $8.60 a box" in answer and not events[-1]["violations"]
