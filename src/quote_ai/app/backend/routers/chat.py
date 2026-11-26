"""/api/chat: the chat with the agent, on the quote page (AI-18), streamed with server-sent events.

  POST /api/chat/{quote_id}            {"text": ...} -> text/event-stream:
                                         step     a tool was called ("Checking eligibility...")
                                         source   AI-19: where a tool result came from, as a badge (BADGES: one table,
                                                  from the tool result's "source", never from the model's text)
                                         proposal a transition the agent proposes (nothing written)
                                         memory   AI-20: a customer fact the agent proposes to save (nothing written)
                                         token    a piece of the checked answer
                                         plan     AI-21 (design B): the orchestrator's route and whether a rule or a
                                                  model chose it
                                         done     the trace ID (feedback buttons, AI-22), why the loop stopped, the
                                                  rule facts, any violation, tokens
  GET  /api/chat/{quote_id}            AI-20: the conversation so far (resume a quote where it was left)
  POST /api/chat/{quote_id}/memory     AI-20: the rep confirms a customer fact -> QuoteService.confirm_fact
  POST /api/chat/{quote_id}/confirm    the rep confirms a proposal -> QuoteService, which checks the rules: a move the
                                       rules don't allow is refused (409 / 422), exactly as from any other screen

Every question carries the quote and its confirmed requirements (the chat lives on the quote page). The route only
relays: rule results come from the tools, model calls go through Unity Gateway, and from AI-20 the agent runs on the
Agent Runtime (ctx.agent is a RemoteAgent). The app never calls a model itself and never writes a quote except
through QuoteService.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from quote_ai.domain.models import QuoteState as S

from ..auth import User, rep
from ..context import Context
from ..deps import context
from .quotes import _own, view

router = APIRouter(prefix="/api/chat", tags=["chat"])

STEP_LABELS = {"search_candidates": "Searching candidates", "check_eligibility": "Checking eligibility",
               "get_evidence": "Reading the evidence", "get_device": "Reading the GUDID record",
               "check_fulfilment": "Checking fulfilment", "price_quote": "Pricing",
               "win_probability": "Win probability", "propose_transition": "Proposing a step",
               "gudid_live_lookup": "Checking the live GUDID record", "ask_genie": "Asking the sales analyst",
               "knowledge_search": "Searching the documents", "load_skill": "Loading the quoting playbook",
               "propose_memory": "Proposing a customer fact"}
# AI-19: tool result source -> the badge under the answer
BADGES = {"accessgudid_live": "Live GUDID record, retrieved today",
          "lakehouse_copy": "Lakehouse copy, as of the last daily update",
          "genie": "Sales data (Genie)", "skill": "DBxLab quoting playbook", "rules": "DBxLab rules",
          "model": "Win model (simulation, demo data)", "ai_search": "Documents (AI Search)",
          "proposal": "Proposal (nothing written)", "unavailable": "Unavailable", "refused": "Refused"}


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class FactIn(BaseModel):
    fact_key: str = Field(min_length=1, max_length=80)
    fact_value: str = Field(min_length=1, max_length=200)


class ConfirmIn(BaseModel):
    target: S
    revision: int


def sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def source_view(e: dict) -> dict:
    """The badge for one tool result, with what makes it checkable: the as-of date, the record version, the SQL."""
    out, src = e["output"], e.get("source") or "rules"
    badge = BADGES.get(src, src)
    if src == "lakehouse_copy" and out.get("as_of"):
        badge = f"Lakehouse copy, as of {out['as_of']}"
    if src == "accessgudid_live" and out.get("public_version_number"):
        badge += f" (version {out['public_version_number']})"
    if src == "skill" and out.get("sections"):
        badge += f" ({', '.join(out['sections'][:2])})"
    view_ = {"tool": e["name"], "source": src, "badge": badge, "served": e.get("served")}
    if src == "genie" and out.get("sql"):
        view_["sql"] = out["sql"]
    if out.get("fallback"):
        view_["fallback"] = out["fallback"]
    if src == "unavailable":
        view_["missing"] = out.get("missing")
    return view_


@router.post("/{quote_id}")
def chat(quote_id: str, body: ChatIn, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    if ctx.agent is None:
        raise HTTPException(status_code=503, detail="the agent is not configured in this app")
    history = ctx.memory.messages(user.id, quote_id) if ctx.memory else []
    facts = ctx.memory.facts(q.account, user.id) if ctx.memory else []
    messages = history + [{"role": "user", "content": body.text}]

    def events():
        done = None
        for e in ctx.agent.events(messages, quote=q, rep=user.id, customer_facts=facts):
            t = e["type"]
            if t == "step":
                yield sse("step", {"name": e["name"], "label": STEP_LABELS.get(e["name"], e["name"])})
            elif t == "tool_result":
                yield sse("source", source_view(e))
            elif t == "proposal":
                yield sse("proposal", e["proposal"])
            elif t == "memory_proposal":
                yield sse("memory", e["proposal"])
            elif t == "token":
                yield sse("token", e["text"])
            elif t == "plan":                        # AI-21: the orchestrator's route, by rule or by model
                yield sse("plan", {k: e[k] for k in ("route", "by", "state")})
            elif t == "done":
                done = e
        if done is None:
            yield sse("error", {"detail": "the agent ended without an answer"})
            return
        if ctx.memory:                               # AI-20: the conversation outlives the session
            ctx.memory.append(user.id, quote_id, "user", body.text)
            ctx.memory.append(user.id, quote_id, "assistant", done["answer"])
        yield sse("done", {k: done.get(k) for k in ("trace_id", "stop_reason", "facts", "violations", "usage")})
    return StreamingResponse(events(), media_type="text/event-stream")


@router.get("/{quote_id}")
def conversation(quote_id: str, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    if ctx.memory is None:
        return {"quote_id": q.quote_id, "messages": [], "customer_facts": []}
    return {"quote_id": q.quote_id, "messages": ctx.memory.messages(user.id, quote_id),
            "customer_facts": ctx.memory.facts(q.account, user.id),
            "suggested_requirements": ctx.memory.suggested_requirements(q.account, user.id, q.requirements)}


@router.post("/{quote_id}/memory", status_code=201)
def confirm_fact(quote_id: str, body: FactIn, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    if ctx.memory is None:
        raise HTTPException(status_code=503, detail="memory is not configured in this app")
    return ctx.service.confirm_fact(q.account, body.fact_key, body.fact_value, user.id)


@router.post("/{quote_id}/confirm")
def confirm_proposal(quote_id: str, body: ConfirmIn, user: User = Depends(rep), ctx: Context = Depends(context),
                     idempotency_key: str | None = Header(default=None)):
    """The rep confirms a proposal; QuoteService decides. Only moves with no data of their own are made here
    (cancel, submit, issue); the others are on their screens (shortlist, pricing)."""
    q = _own(ctx, quote_id, user)
    key = idempotency_key or f"chat-{uuid.uuid4().hex}"
    svc = ctx.service
    if body.target == S.ISSUED:
        if ctx.inputs_as_of is None:
            raise HTTPException(status_code=503, detail="the inputs' freshness is not configured in this app")
        q = svc.issue(quote_id, body.revision, user.id, key=key, inputs_as_of=ctx.inputs_as_of(q))
    elif body.target == S.PENDING_APPROVAL:
        q = svc.submit(quote_id, body.revision, user.id, key=key)
    elif body.target == S.CANCELLED:
        q = svc.change_state(quote_id, body.revision, user.id, S.CANCELLED)
    else:
        raise HTTPException(status_code=409, detail=f"{body.target.value} is reached on its own screen")
    return view(ctx, q)
