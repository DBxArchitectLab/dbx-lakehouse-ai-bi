"""Rule-based orchestration (AI-21): a deterministic workflow inside an agentic app.

The quote lifecycle is a fixed workflow, the state machine (rules/quote_states.py); agents work inside its steps.
handle() checks three things, in order:
  1. an explicit action (a button: price, submit, approve, issue, revise, cancel) goes straight to its handler, which
     calls QuoteService: the agents propose, QuoteService commits
  2. a free-text question is the only place a model routes, to knowledge, analyst or catalog; a question never
     changes the quote's state
  3. everything else goes to the specialist for the quote's current state (ROUTES)
Whether approval is needed is never a model's choice: pricing.approval_required decides (the pricing specialist and
approval_check are rules, not LLMs). The orchestrator's routing is code, not tokens. Supervisor Agent (Agent Bricks)
is legacy for this, so this is our own code, deployed with the agent on the Agent Runtime.

events() has the same shape as QuoteAgent.events, so the app's chat can use either design (QUOTE_AI_DESIGN).
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from quote_ai.agents.specialists import analyst, catalog, knowledge, pricing as pricing_specialist
from quote_ai.domain.models import QuoteState as S
from quote_ai.rules import approval as approval_rules

ROUTES = {
    "DRAFT": "requirements",            # extraction (AI-05) + confirm
    "REQUIREMENTS_CONFIRMED": "catalog",
    "SHORTLISTED": "pricing",
    "PRICED": "approval_check",
    "PENDING_APPROVAL": "wait_for_manager",
    "APPROVED": "issue_check",
}
QUESTION_ROUTES = ("knowledge", "analyst", "catalog")
ACTIONS = ("price", "submit", "approve", "issue", "revise", "cancel")
ROUTER = ("ROUTER. Which specialist answers this question? Answer with one word: knowledge (explanations, documents, "
          "why products differ), analyst (sales history, prices paid, win rates) or catalog (products, eligibility, "
          "stock).")
_QUESTION = re.compile(r"^\s*(why|what|how|is|are|does|do|can|could|which|who|when|where|tell)\b|\?\s*$", re.I)


@dataclass
class Turn:
    text: str = ""
    action: str | None = None
    args: dict = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)

    def is_question(self) -> bool:
        return self.action is None and bool(_QUESTION.search(self.text or ""))


class Orchestrator:
    def __init__(self, deps, service=None, client=None, endpoint: str | None = None,
                 groups: Callable[[str], set[str]] | None = None,
                 inputs_as_of: Callable[[Any], dict] | None = None, tier: str = "standard") -> None:
        self.deps, self.service, self.client, self.endpoint = deps, service, client, endpoint
        self.groups, self.inputs_as_of, self.tier = groups or (lambda u: set()), inputs_as_of, tier
        self.specialists = {"catalog": catalog.make(deps, client, endpoint),
                            "knowledge": knowledge.make(deps, client, endpoint),
                            "analyst": analyst.make(deps, client, endpoint)}

    # ------------------------------------------------------------ routing

    def route_question(self, turn: Turn) -> tuple[str, dict]:
        """The one model-based route: a small call, one word back, checked against the allowed routes."""
        r = self.client.chat.completions.create(model=self.endpoint, temperature=0, messages=[
            {"role": "system", "content": ROUTER}, {"role": "user", "content": turn.text}])
        word = (r.choices[0].message.content or "").strip().lower().split()
        route = word[0] if word and word[0] in QUESTION_ROUTES else "catalog"
        return route, {"prompt_tokens": int(getattr(r.usage, "prompt_tokens", 0) or 0),
                       "completion_tokens": int(getattr(r.usage, "completion_tokens", 0) or 0), "model_calls": 1}

    def route(self, turn: Turn, quote) -> tuple[str, str]:
        """(route, by): by is action, model or rule. Free text that isn't a question follows the state."""
        if turn.action:
            if turn.action not in ACTIONS:
                raise ValueError(f"unknown action {turn.action!r}")
            return turn.action, "action"
        if turn.is_question():
            return "question", "model"
        return ROUTES.get(quote.state.value, "done"), "rule"

    # ------------------------------------------------------------ handling

    def handle(self, turn: Turn, quote, rep: str, customer_facts: list[dict] | None = None) -> Iterator[dict]:
        route, by = self.route(turn, quote)
        extra = {}
        if route == "question":
            route, extra = self.route_question(turn)
        yield {"type": "plan", "route": route, "by": by, "state": quote.state.value, "version": quote.version}
        if by == "action":
            yield from self._action(turn, quote, rep)
            return
        messages = turn.history + [{"role": "user", "content": turn.text or _DEFAULT.get(route, "")}]
        if route in self.specialists:
            for e in self.specialists[route].events(messages, quote, rep, customer_facts):
                if e["type"] == "done" and extra:
                    e = e | {"usage": {k: e["usage"].get(k, 0) + extra.get(k, 0) for k in e["usage"]}}
                yield e
        elif route == "pricing":
            yield from pricing_specialist.plan(self.deps, quote, rep)
        else:
            yield from _say(self._rule_answer(route, quote))

    def events(self, messages: list[dict], quote=None, rep: str | None = None,
               customer_facts: list[dict] | None = None, **_) -> Iterator[dict]:
        """The chat's door: the last user message is the turn; the rest is its history."""
        last = messages[-1]["content"] if messages else ""
        yield from self.handle(Turn(text=last, history=list(messages[:-1])), quote, rep or quote.rep, customer_facts)

    def _rule_answer(self, route: str, q) -> str:
        if route == "requirements":
            missing = ", ".join(q.requirements.missing) or "nothing"
            return f"Requirements r{q.requirements.version}: missing {missing}. Confirm them on the Confirm screen."
        if route == "approval_check":
            return {"NONE": "Within the rep's limit: submit approves it by rule.",
                    "MANAGER": "The discount is in the manager's band: submit sends this version for approval."
                    }.get(q.approval_needed or "", "Not priced by the policy yet: price it on the Pricing screen.")
        if route == "wait_for_manager":
            return f"Version {q.version} is waiting for a sales manager's approval."
        if route == "issue_check":
            ok = approval_rules.can_issue(q, q.approval)
            return ("Approved for this version: it can be issued on fresh inputs." if ok else
                    "The approval does not match this version: it needs a new approval.")
        return f"The quote is {q.state.value}: nothing more to do."

    def _action(self, turn: Turn, q, rep: str) -> Iterator[dict]:
        svc, a, key = self.service, turn.args, turn.args.get("key") or f"orch-{uuid.uuid4().hex}"
        if turn.action == "price":
            cat = self.deps.catalog
            new = svc.price(q.quote_id, q.revision, rep, self.tier, cat.price_book(), a.get("asked_unit_price"),
                            cat.packaging([l.sku_id for l in q.lines if l.sku_id]))
        elif turn.action == "submit":
            new = svc.submit(q.quote_id, q.revision, rep, key=key)
        elif turn.action == "approve":
            new = svc.approve(q.quote_id, q.revision, rep, self.groups(rep), key=key,
                              expected_version=a.get("version", q.version))
        elif turn.action == "issue":
            new = svc.issue(q.quote_id, q.revision, rep, key=key, inputs_as_of=self.inputs_as_of(q))
        elif turn.action == "cancel":
            new = svc.change_state(q.quote_id, q.revision, rep, S.CANCELLED)
        else:
            cat = self.deps.catalog
            new = svc.revise_terms(q.quote_id, q.revision, rep, self.tier, cat.price_book(),
                                   delivery_days=a.get("delivery_days"), asked_unit_price=a.get("asked_unit_price"),
                                   packaging=cat.packaging([l.sku_id for l in q.lines if l.sku_id]))
        yield {"type": "action", "action": turn.action, "state": new.state.value, "version": new.version,
               "revision": new.revision}
        yield from _say(f"{turn.action}: the quote is {new.state.value}, version {new.version}.")


_DEFAULT = {"catalog": "Find the options for this quote, check them and their fulfilment."}


def _say(text: str) -> Iterator[dict]:
    yield {"type": "token", "text": text}
    yield {"type": "done", "answer": text, "stop_reason": "rules", "violations": [], "facts": {}, "calls": [],
           "usage": {"prompt_tokens": 0, "completion_tokens": 0, "model_calls": 0, "seconds": 0.0},
           "trace_id": None}
