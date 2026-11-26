"""/api/quotes: thin routes over QuoteService. No route writes a table: a route that did would skip the rules.

  POST /api/quotes                    read a request -> DRAFT (r1, and what is missing)
  GET  /api/quotes                    my quotes, each with its events
  GET  /api/quotes/{id}               one quote and its events
  POST /api/quotes/{id}/confirm       the rep's answers -> REQUIREMENTS_CONFIRMED (r2, one line per size)
  POST /api/quotes/{id}/quick-quote   v1 simple rules -> SHORTLISTED -> PRICED
  POST /api/quotes/{id}/state         a move by state alone; the ALLOWED table decides (409 with the reason)
AI-20: My quotes reads the rep's quotes from Lakebase (the repository QuoteService was given); ?open=true leaves out
the finished ones. GET /api/quotes/{id}/resume reopens one: its state and shortlist (as before) and, new, its
conversation and the customer's confirmed facts, so the chat continues where it was. Another rep's quote is not found.
AI-20: confirm takes from_memory, requirements the rep confirmed from the customer's remembered facts.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from quote_ai.domain.models import Quote
from quote_ai.rules import simple_rules
from quote_ai.storage.repository import QuoteNotFound

from ..auth import User, rep
from ..context import Context
from ..deps import as_rep, context
from ..models import Confirm, NewRequest, QuickQuote, StateChange

router = APIRouter(prefix="/api/quotes", tags=["quotes"])


def view(ctx: Context, q: Quote, events: bool = True) -> dict:
    out = q.model_dump(mode="json") | {"total": q.total}
    if q.price_method == simple_rules.PRICE_METHOD:
        out["label"] = simple_rules.LABEL
    if events:
        out["events"] = [e.model_dump(mode="json") for e in ctx.service.repo.events(q.quote_id)]
    return out


def check_account(ctx: Context, account: str, user: User) -> None:
    """AI-24: may this rep work on this account? Asked of Unity Catalog AS THE REP (the row filter on
    quote_ai_sales.rep_account decides), not of a filter in this code. A manager sees every account."""
    from quote_ai.rules.access import NO_ACCESS
    if account not in set(as_rep(ctx, user).accounts().values()):
        raise HTTPException(status_code=403, detail=NO_ACCESS)


def _own(ctx: Context, quote_id: str, user: User) -> Quote:
    """A quote the rep may open: its account is one Unity Catalog lets them read (AI-24). Until AI-23 this was a
    filter by rep in code (q.rep != user.id: not found), and every path that skipped it (Genie) saw everything."""
    q = ctx.service.repo.get(quote_id)
    check_account(ctx, q.account, user)
    return q


@router.post("", status_code=201)
def read_request(body: NewRequest, user: User = Depends(rep), ctx: Context = Depends(context)):
    check_account(ctx, body.account, user)                     # AI-24: only for an account the rep may read
    q = ctx.service.read_request(body.account, user.id, body.text, ctx.extractor)
    return view(ctx, q)


FINISHED = {"ISSUED", "EXPIRED", "CANCELLED", "REJECTED"}


@router.get("")
def my_quotes(open: bool = False, user: User = Depends(rep), ctx: Context = Depends(context)):
    return [view(ctx, q) for q in ctx.service.mine(user.id) if not (open and q.state.value in FINISHED)]


@router.get("/{quote_id}/resume")
def resume(quote_id: str, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    out = {"quote": view(ctx, q), "messages": [], "customer_facts": [], "suggested_requirements": {}}
    if ctx.memory is not None:
        out |= {"messages": ctx.memory.messages(user.id, quote_id),
                "customer_facts": ctx.memory.facts(q.account, user.id),
                "suggested_requirements": ctx.memory.suggested_requirements(q.account, user.id, q.requirements)}
    return out


@router.get("/{quote_id}")
def get_quote(quote_id: str, user: User = Depends(rep), ctx: Context = Depends(context)):
    return view(ctx, _own(ctx, quote_id, user))


@router.post("/{quote_id}/confirm")
def confirm(quote_id: str, body: Confirm, user: User = Depends(rep), ctx: Context = Depends(context)):
    _own(ctx, quote_id, user)
    q = ctx.service.confirm(quote_id, body.revision, user.id, body.quantity_by_size, body.unit, body.gloves_per_unit,
                            from_memory=body.from_memory or None)
    return view(ctx, q)


@router.post("/{quote_id}/quick-quote")
def quick_quote(quote_id: str, body: QuickQuote, user: User = Depends(rep), ctx: Context = Depends(context)):
    _own(ctx, quote_id, user)
    q = ctx.service.quick_quote(quote_id, body.revision, user.id, ctx.catalog.offerable_skus(),
                                ctx.catalog.price_book())
    return view(ctx, q)


@router.post("/{quote_id}/state")
def change_state(quote_id: str, body: StateChange, user: User = Depends(rep), ctx: Context = Depends(context)):
    _own(ctx, quote_id, user)
    return view(ctx, ctx.service.change_state(quote_id, body.revision, user.id, body.to))

