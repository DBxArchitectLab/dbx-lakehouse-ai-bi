"""/api/approvals (AI-21, QuoteAI v3): the Approval inbox, for managers only, and who the signed-in user is.

  GET  /api/approvals/me                    the signed-in user and whether they approve: the React menu shows the
                                            inbox by role (a hidden page is not a control: the API checks again)
  GET  /api/approvals                       managers only (403 otherwise): every quote in PENDING_APPROVAL with what
                                            the manager approves: SKUs, boxes, price, discount, margin, the evidence
                                            snapshot, the version and its terms hash
  POST /api/approvals/{id}/v/{version}      + Idempotency-Key: approve that exact version -> QuoteService.approve
                                            (409 "quote changed; reload" if it changed; 403 for self-approval or a
                                            non-manager); the router has no rules of its own
  POST /api/approvals/{id}/v/{version}/reject  {"reason"}

The approver is the identity Databricks Apps forwards (X-Forwarded-Email), never a field in the request; the group
(DBxLab_Sales_Managers) is looked up for that identity (ctx.groups).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from quote_ai.rules import approval as approval_rules
from quote_ai.rules.approval import terms_hash

from ..auth import User, rep
from ..context import Context
from ..deps import as_rep, context
from .quotes import view

router = APIRouter(prefix="/api/approvals", tags=["approvals"])


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


def groups_of(ctx: Context, user: User) -> set[str]:
    return ctx.groups(user.id) if ctx.groups else set()


def summary(ctx: Context, q, book=None) -> dict:
    """What the manager approves: the terms of this version, with the margin from the price book's cost. AI-24: the
    book is read as the manager (the cost is visible to managers only)."""
    cost = {r.id: r.cost for r in (book if book is not None else ctx.catalog.price_book())}
    lines = [{"size": l.size, "sku_id": l.sku_id, "boxes": l.quantity, "unit_price": l.unit_price,
              "list_price": l.list_price,
              "margin_pct": round(100 * (l.unit_price / cost[l.price_book_id] - 1), 2)
              if l.unit_price and cost.get(l.price_book_id) else None} for l in q.lines]
    return {"quote_id": q.quote_id, "account": q.account, "rep": q.rep, "version": q.version,
            "revision": q.revision, "state": q.state.value, "product_id": q.product_id, "lines": lines,
            "total": q.total, "discount_pct": round(100 * (q.discount or 0), 2),
            "approval_needed": q.approval_needed, "requirements_version": q.requirements.version,
            "delivery_days": q.requirements.delivery_days, "evidence_snapshot": q.evidence_snapshot,
            "inventory_snapshot": q.inventory_snapshot, "terms_hash": terms_hash(q)}


@router.get("/me")
def me(user: User = Depends(rep), ctx: Context = Depends(context)):
    groups = groups_of(ctx, user)
    return {"user": user.id, "groups": sorted(groups), "is_manager": approval_rules.is_approver(groups)}


@router.get("")
def inbox(user: User = Depends(rep), ctx: Context = Depends(context)):
    if not approval_rules.is_approver(groups_of(ctx, user)):
        raise HTTPException(status_code=403, detail="managers only")
    book = as_rep(ctx, user).price_book()
    return [summary(ctx, q, book) for q in ctx.service.pending_approval()]


@router.post("/{quote_id}/v/{version}")
def approve_version(quote_id: str, version: int, user: User = Depends(rep), ctx: Context = Depends(context),
                    idempotency_key: str | None = Header(default=None)):
    if not idempotency_key:
        raise HTTPException(status_code=428, detail="an Idempotency-Key header is required to approve")
    q = ctx.service.repo.get(quote_id)
    q = ctx.service.approve(quote_id, q.revision, user.id, groups_of(ctx, user), key=idempotency_key,
                            expected_version=version)
    return view(ctx, q)


@router.post("/{quote_id}/v/{version}/reject")
def reject_version(quote_id: str, version: int, body: RejectIn, user: User = Depends(rep),
                   ctx: Context = Depends(context)):
    q = ctx.service.repo.get(quote_id)
    if q.version != version:
        raise HTTPException(status_code=409, detail="quote changed; reload")
    approval_rules.check_approver(q, user.id, groups_of(ctx, user))
    return view(ctx, ctx.service.reject(quote_id, q.revision, user.id, body.reason))
