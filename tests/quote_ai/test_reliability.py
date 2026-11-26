"""When a tool misbehaves (AI-18, AI-19): timeouts, retries with back-off, stop conditions, duplicates, partial
failure. A clear stop beats a confident guess."""
import time

import pytest

from agent_fixtures import NO_WAIT, ScriptedClient, agent, confirmed, toolbox, user
from quote_ai.agents import reliability as r
from quote_ai.agents.loop_by_hand import run
from quote_ai.agents.standin import StandInClient


def test_a_transient_error_is_retried_three_times_after_1_2_4_seconds():
    waits, calls = [], []

    def flaky(name, args):
        calls.append(name)
        raise r.ServiceUnavailable("503")
    attempts = []
    with pytest.raises(r.ToolUnavailable) as e:
        r.call_tool("get_device", {}, flaky, sleep=waits.append, rand=lambda: 0.0, attempts=attempts)
    assert len(calls) == 4 and waits == [1.0, 2.0, 4.0] and len(e.value.attempts) == 4


def test_the_jitter_is_small():
    assert 1.0 <= r.backoff(0) <= 1.3 and 4.0 <= r.backoff(2) <= 4.3


def test_a_slow_call_is_cut_off_at_the_timeout():
    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        r.run_with_timeout(lambda a: time.sleep(1.0), {}, timeout=0.05)
    assert time.perf_counter() - start < 0.5


def test_a_rule_answer_is_never_retried():
    _, q = confirmed()
    box, calls = toolbox(q), []

    def counting(name, args):
        calls.append(name)
        return box.run(name, args)
    out = r.call_tool("price_quote", {"product_id": "GLOVE-F"}, counting, sleep=lambda s: None)
    assert "refused" in out and calls == ["price_quote"]


def test_a_permission_refusal_is_never_retried():
    calls = []

    def denied(name, args):
        calls.append(name)
        raise r.ToolRefused("PERMISSION_DENIED: no EXECUTE on cat.quote_ai_sales.price_book")
    out, how = r.Turn().call("read_price_book", {}, denied, sleep=lambda s: None)
    assert how == "refused" and out["error"] == "PERMISSION_DENIED" and calls == ["read_price_book"]


def test_a_tool_that_times_out_ends_in_a_clear_stop():
    """AI-18's demo: get_device slow on purpose; a timeout, retries after 1, 2 and 4 s, then a clear stop."""
    _, q = confirmed()
    box = toolbox(q)
    slow = box.run

    def slow_device(name, args):
        if name == "get_device":
            time.sleep(0.2)
        return slow(name, args)
    box.run = slow_device
    script = ScriptedClient([[("get_device", {"product_id": "GLOVE-A"})], "Glove A is current."])
    out = run([user("Show me Glove A's GUDID record")], box, script, "test", partial=False,
              retry={"sleep": lambda s: None, "timeout": 0.05})
    assert out.stop_reason == "tool_unavailable" and script.calls == 1
    assert out.answer.startswith("Stopped: the device record could not be reached after 4 attempts")
    result = next(e for e in out.events if e["type"] == "tool_result")
    assert [a["waited_s"] for a in result["output"]["attempts"]][:3] == [pytest.approx(1, abs=0.3),
                                                                         pytest.approx(2, abs=0.3),
                                                                         pytest.approx(4, abs=0.3)]


def test_the_same_call_twice_is_no_progress():
    _, q = confirmed()
    call = [("check_eligibility", {"product_ids": ["GLOVE-A"]})]
    out = run([user("Is Glove A eligible?")], toolbox(q), ScriptedClient([call, call, call]), "test")
    assert out.stop_reason == "no_progress" and out.tools_called == ["check_eligibility"] * 2
    served = [e["served"] for e in out.events if e["type"] == "tool_result"]
    assert served == ["run", "duplicate"]


def test_the_step_limit_stops_a_confused_model():
    _, q = confirmed()
    turns = [[("get_evidence", {"product_id": f"GLOVE-{c}"})] for c in "ABCDEFGHIJ"]
    out = run([user("Tell me everything")], toolbox(q), ScriptedClient(turns), "test", max_steps=8)
    assert out.stop_reason == "max_steps" and len(out.tools_called) == 8


# ------------------------------------------------------------------ AI-19: duplicates and partial failure

def test_the_same_lookup_twice_is_one_api_call():
    _, q = confirmed()
    box = toolbox(q)
    turn = r.Turn()
    di = box.run("get_device", {"product_id": "GLOVE-A"})["primary_di"]
    a, how_a = turn.call("gudid_live_lookup", {"di": di}, box.run)
    b, how_b = turn.call("gudid_live_lookup", {"di": di}, box.run)
    c = box.run("gudid_live_lookup", {"di": di})                          # a later turn: the 24 h cache
    assert (how_a, how_b) == ("run", "duplicate") and a is b and c["cached"]
    assert box.deps.live_gudid.calls == 1 and a["source"] == "accessgudid_live"


def test_genie_down_the_answer_names_the_missing_history_and_invents_no_prices():
    def paused(question):
        raise RuntimeError("Genie space is paused")
    _, q = confirmed()
    a = agent(genie=paused)
    events = list(a.events([user("What has Lakeside bought from us in the last year, and at what price? "
                                 "And is Glove A current?")], q, "rep.alice", retry=NO_WAIT))
    done = events[-1]
    genie = [e for e in events if e["type"] == "tool_result" and e["name"] == "ask_genie"]
    assert len(genie) == 1 and genie[0]["output"]["unavailable"] == "ask_genie"
    assert len(genie[0]["output"]["attempts"]) == 4
    assert "Buying history is unavailable right now" in done["answer"]
    assert "Glove A" in done["answer"] and "live GUDID record" in done["answer"]
    assert "a box on average" not in done["answer"] and not done["violations"]


def test_live_calls_are_limited_per_conversation():
    turn = r.Turn(limits={"gudid_live_lookup": 2})
    for di in ("1", "2"):
        turn.call("gudid_live_lookup", {"di": di}, lambda n, a: {"ok": a})
    out, how = turn.call("gudid_live_lookup", {"di": "3"}, lambda n, a: {"ok": a})
    assert how == "limit" and "at most 2" in out["reason"]


def test_a_down_tool_is_not_called_again_in_the_turn():
    calls = []

    def down(name, args):
        calls.append(args)
        raise TimeoutError("slow")
    turn = r.Turn(missing={"ask_genie": "buying history"})
    turn.call("ask_genie", {"question": "a"}, down, sleep=lambda s: None, timeout=1)
    out, how = turn.call("ask_genie", {"question": "b"}, down, sleep=lambda s: None, timeout=1)
    assert how == "down" and len(calls) == 4 and out["missing"] == "buying history"


def test_the_overlapping_tool_makes_the_choice_a_coin_flip():
    """AI-19's break: two descriptions that both say "find gloves" -> the stand-in can't tell them apart."""
    from quote_ai.agents.standin import _Ctx
    from quote_ai.agents.tools import SPECS, spec
    find = spec("find_gloves", "Find gloves for a customer.", {}, [])
    picks = {_Ctx([user(f"Find nitrile gloves for account {i}")], [SPECS["search_candidates"], find]).pick()
             for i in range(12)}
    assert picks == {"search_candidates", "find_gloves"}
    assert _Ctx([user("Find nitrile gloves")], [SPECS["search_candidates"], SPECS["knowledge_search"]]).pick() \
        == "search_candidates"


def test_stand_in_reports_tokens_so_a_long_prompt_shows():
    c = StandInClient()
    short = c.chat.completions.create(messages=[user("hi")], tools=[])
    long = c.chat.completions.create(messages=[{"role": "system", "content": "x" * 4000}, user("hi")], tools=[])
    assert long.usage.prompt_tokens > short.usage.prompt_tokens + 900
