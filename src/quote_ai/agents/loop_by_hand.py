"""The tool-calling loop, by hand (AI-18): plain Python, so nothing is hidden.

Call the model with the messages and the tool specs. If it asks for no tool, that is the answer. Otherwise run each
tool call and append the result as a tool message, and repeat. Stop when the model answers, after max_steps (8), when
a step only repeats calls already answered (no progress), or when a tool still fails after its retries (AI-18 stops
and says so; with partial=True, from AI-19, the answer names what is missing instead). Every model and tool call is a
span in the MLflow trace when MLflow is there.

run_stream yields the events the app streams (AI-18, AI-19): step (a tool was called), tool_result (with its source),
proposal / memory_proposal (written nowhere), token (a piece of the answer) and done (the answer, why the loop
stopped, tokens, the rule facts and any violation of them). The answer is checked against the tool outputs before it
is sent (boundary.check_answer); text tokens are pieces of the checked answer, not raw model tokens.

  python -m quote_ai.agents.loop_by_hand --request lakeside-001            # the local demo (stand-in model)
  python -m quote_ai.agents.loop_by_hand --request lakeside-001 --endpoint quote_ai_chat_dev
"""
from __future__ import annotations

import argparse
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from quote_ai.agents import boundary
from quote_ai.agents.reliability import Turn
from quote_ai.agents.tools import MISSING, Toolbox, dumps
from quote_ai.agents.tracing import last_trace_id, span, trace

SYSTEM = ("You help DBxLab sales reps quote gloves. Use tools for every fact. Eligibility and prices come only from "
          "tools; never change them. If a check is unknown, say evidence is needed.")
SYSTEM_AI19 = ("If a tool result says unavailable, tell the rep which information is missing; do not answer that "
               "part from memory. Every fact needs a source from a tool result. You cannot change a quote: propose a "
               "transition and the rep confirms it.")
MAX_STEPS = 8
LIVE_CALLS_PER_CONVERSATION = 5


def system_prompt(quote=None, skills: list[dict] | None = None, customer_facts: list[dict] | None = None) -> str:
    """The system prompt: the rules, the quote the chat is on (state, version, confirmed requirements, lines), the
    skills that can be loaded (name and description only: the body loads on demand), and confirmed customer facts."""
    parts = [SYSTEM, SYSTEM_AI19]
    if quote is not None:
        r = quote.requirements
        req = {k: v for k, v in r.model_dump().items() if v not in (None, [], {}) and k != "missing"}
        parts.append(f"Quote: {quote.quote_id} · state {quote.state.value} · version {quote.version} · account "
                     f"{quote.account} · product {quote.product_id or 'none'}")
        parts.append(f"Confirmed requirements (r{r.version}): {json.dumps(req, sort_keys=True, default=str)}")
        if quote.lines:
            parts.append("Lines: " + ", ".join(f"{l.size} {l.quantity} {l.unit}" for l in quote.lines))
    if skills:
        parts.append("Skills (load_skill when one applies): "
                     + "; ".join(f"{s['name']}: {s['description']}" for s in skills))
    if customer_facts:
        parts.append("Customer facts confirmed by a rep (memory informs; it becomes a requirement only when the rep "
                     "confirms it on the quote): "
                     + "; ".join(f"{f['fact_key']} = {f['fact_value']}" for f in customer_facts))
    return "\n".join(parts)


@dataclass
class RunResult:
    answer: str
    stop_reason: str
    events: list[dict] = field(default_factory=list)

    @property
    def done(self) -> dict:
        return self.events[-1]

    @property
    def tools_called(self) -> list[str]:
        return [e["name"] for e in self.events if e["type"] == "step"]

    @property
    def proposals(self) -> list[dict]:
        return boundary.proposals(self.events)

    @property
    def violations(self) -> list[str]:
        return self.done.get("violations", [])

    @property
    def usage(self) -> dict:
        return self.done.get("usage", {})


def _tool_calls(m) -> list:
    return list(getattr(m, "tool_calls", None) or [])


def _assistant(m, calls) -> dict:
    return {"role": "assistant", "content": getattr(m, "content", None),
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in calls]}


def _args(text: str | None) -> dict:
    try:
        a = json.loads(text or "{}")
        return a if isinstance(a, dict) else {}
    except json.JSONDecodeError:
        return {}


def _chunks(text: str, size: int = 24) -> Iterator[str]:
    words = text.split(" ")
    for i in range(0, len(words), size):
        yield " ".join(words[i:i + size]) + (" " if i + size < len(words) else "")


def run_stream(messages: list[dict], toolbox: Toolbox, client: Any, endpoint: str, max_steps: int = MAX_STEPS,
               partial: bool = True, system: str | None = None, retry: dict | None = None) -> Iterator[dict]:
    msgs = [{"role": "system", "content": system or system_prompt(toolbox.quote)}] + list(messages)
    turn = Turn(missing=MISSING, limits={"gudid_live_lookup": LIVE_CALLS_PER_CONVERSATION})
    outputs: list[tuple[str, dict, dict]] = []
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0, "seconds": 0.0}
    answer, stop = "", None
    started = time.perf_counter()
    for _ in range(max_steps):
        with span("model", "CHAT_MODEL", {"messages": len(msgs)}) as s:
            r = client.chat.completions.create(model=endpoint, messages=msgs, tools=toolbox.specs(), temperature=0)
            s.set_outputs({"tool_calls": len(_tool_calls(r.choices[0].message))})
        usage["model_calls"] += 1
        usage["prompt_tokens"] += int(getattr(r.usage, "prompt_tokens", 0) or 0)
        usage["completion_tokens"] += int(getattr(r.usage, "completion_tokens", 0) or 0)
        m = r.choices[0].message
        calls = _tool_calls(m)
        if not calls:
            answer, stop = m.content or "", "answered"
            break
        msgs.append(_assistant(m, calls))
        fresh = 0
        for c in calls:
            name, args = c.function.name, _args(c.function.arguments)
            yield {"type": "step", "name": name, "args": args, "call_id": c.id}
            t0 = time.perf_counter()
            with span(name, "TOOL", args) as s:
                out, how = turn.call(name, args, toolbox.run, **(retry or {}))
                s.set_outputs(out)
            fresh += how != "duplicate"
            outputs.append((name, args, out))
            yield {"type": "tool_result", "name": name, "call_id": c.id, "output": out, "served": how,
                   "source": out.get("source") or ("unavailable" if "unavailable" in out else
                                                   "refused" if "refused" in out else None),
                   "seconds": round(time.perf_counter() - t0, 3)}
            if "proposal" in out:
                yield {"type": "proposal", "proposal": out["proposal"]}
            if "memory_proposal" in out:
                yield {"type": "memory_proposal", "proposal": out["memory_proposal"]}
            msgs.append({"role": "tool", "tool_call_id": c.id, "content": dumps(out)})
            if how == "down" and not partial:
                answer, stop = (f"Stopped: {out['missing']} could not be reached after "
                                f"{len(out['attempts'])} attempts. Please try again later."), "tool_unavailable"
                break
        if stop:
            break
        if not fresh:
            answer, stop = ("Stopped: the same tool calls repeated with no new information. Please refine the "
                            "request."), "no_progress"
            break
    else:
        answer, stop = "Stopped after the step limit; please refine the request.", "max_steps"
    users = [m.get("content") or "" for m in messages if m.get("role") == "user"]
    fx = boundary.facts(outputs)
    violations = boundary.check_answer(answer, [o for _, _, o in outputs], users) if stop == "answered" else []
    if violations:
        answer = boundary.withheld(violations, fx)
    for piece in _chunks(answer):
        yield {"type": "token", "text": piece}
    usage["seconds"] = round(time.perf_counter() - started, 3)
    yield {"type": "done", "answer": answer, "stop_reason": stop, "usage": usage, "violations": violations,
           "facts": fx, "calls": turn.log, "trace_id": last_trace_id()}


@trace
def run(messages: list[dict], toolbox: Toolbox, client: Any = None, endpoint: str | None = None,
        max_steps: int = MAX_STEPS, partial: bool = False, system: str | None = None,
        retry: dict | None = None) -> RunResult:
    """The loop by hand. client: an OpenAI-compatible client (default: this workspace's, through Unity Gateway);
    endpoint: the chat model service (default quote_ai_chat_<target>)."""
    if client is None:
        from quote_ai.llm.client import workspace_client
        client = workspace_client()
    if endpoint is None:
        from quote_ai.llm import endpoints
        endpoint = endpoints.model(endpoints.CHAT)
    events = list(run_stream(messages, toolbox, client, endpoint, max_steps, partial, system, retry))
    return RunResult(events[-1]["answer"], events[-1]["stop_reason"], events)


# ------------------------------------------------------------------ the command line (the AI-18 notebook does the same)

REQUESTS = {"lakeside-001": "Find the options for the Lakeside quote, check them and price them."}


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--request", default="lakeside-001", choices=sorted(REQUESTS))
    p.add_argument("--message", help="a follow-up in the same conversation")
    p.add_argument("--endpoint", help="the chat model service; default: the local stand-in model")
    a = p.parse_args(argv)
    from quote_ai.agents import runtime, standin
    deps, quote = runtime.demo_deps(), runtime.demo_quote()
    client = standin.StandInClient() if not a.endpoint else None
    messages = [{"role": "user", "content": REQUESTS[a.request]}]
    for m in ([a.message] if a.message else []):
        first = run(messages, Toolbox(deps, quote, "rep.alice"), client, a.endpoint or "standin", partial=True)
        messages += [{"role": "assistant", "content": first.answer}, {"role": "user", "content": m}]
    for e in run_stream(messages, Toolbox(deps, quote, "rep.alice"), client or _workspace(), a.endpoint or "standin"):
        if e["type"] == "step":
            print(f"step  {e['name']}({json.dumps(e['args'])})")
        elif e["type"] == "tool_result":
            print(f"      -> {e['source']}, {e['seconds']} s, {dumps(e['output'])[:160]}")
        elif e["type"] == "done":
            print("\n" + e["answer"])
            print(f"\nstopped: {e['stop_reason']} · tokens in/out {e['usage']['prompt_tokens']}/"
                  f"{e['usage']['completion_tokens']} · trace {e['trace_id']}")


def _workspace():
    from quote_ai.llm.client import workspace_client
    return workspace_client()


if __name__ == "__main__":
    main()
