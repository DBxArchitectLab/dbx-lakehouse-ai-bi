"""/api/price (AI-21, QuoteAI v3): the Pricing screen. The routes call pricing.py and QuoteService; no rule is copied
into the app.

  GET  /api/price/{quote_id}?product_id=GLOVE-A&asked=8.00
                                          preview, nothing written: price_quote per line (the price book, AI-18) and,
                                          for an asked price, the discount band (NONE, MANAGER, REFUSED) and margin
  POST /api/price/{quote_id}              {"revision", "product_id"?, "asked_unit_price"?} -> the chosen eligible,
                                          on-time product onto the quote (QuoteService.shortlist, rules re-checked),
                                          then QuoteService.price -> PRICED
  POST /api/price/{quote_id}/submit       {"revision"} + Idempotency-Key -> APPROVED by rule (within 5%) or
                                          PENDING_APPROVAL (a manager); REFUSED never gets here (no button)
  POST /api/price/{quote_id}/issue        {"revision"} + Idempotency-Key -> ISSUED, only with an approval of this
                                          version and fresh inputs (StaleEvidence names the stale ones)
  POST /api/price/{quote_id}/revise       {"revision", "delivery_days"?, "product_id"?, "asked_unit_price"?} -> a new
                                          version (r3 when the customer accepts a later date): the approval is void
The React page sends one Idempotency-Key per click: a double click returns the first result.
AI-24: the price book is read as the signed-in rep (as_rep): the row filter and the cost mask apply, the margin floor
comes as floor_price, and margin_pct is shown only to a manager. price_quote takes SKUs, quantities, the tier and an
asked price: no request text reaches it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from quote_ai.rules import pricing

from ..auth import User, rep
from ..context import Context
from ..deps import as_rep, context
from .quotes import _own, view

router = APIRouter(prefix="/api/price", tags=["price"])


class PriceIn(BaseModel):
    revision: int
    product_id: str | None = None
    asked_unit_price: float | None = None


class StepIn(BaseModel):
    revision: int


class ReviseIn(BaseModel):
    revision: int
    delivery_days: int | None = None
    product_id: str | None = None
    asked_unit_price: float | None = None


def _tier(ctx: Context, account: str) -> str:
    return ctx.price_tier(account) if ctx.price_tier else "standard"


def _packaging(ctx: Context, q) -> dict:
    return ctx.catalog.packaging([l.sku_id for l in q.lines if l.sku_id])


def _skus(ctx: Context, q, product_id: str | None) -> list[str]:
    """The SKUs that would price the quote: the chosen product's, per line size, or those already on the quote."""
    if product_id and product_id != q.product_id:
        found = ctx.catalog.products([product_id])
        if not found:
            raise HTTPException(status_code=404, detail=f"product {product_id} not found")
        out = []
        for l in q.lines:
            sku = found[0].sku_for(l.size)
            if sku is None:
                raise HTTPException(status_code=422, detail=f"{product_id} has no size {l.size}")
            out.append(sku.sku_id)
        return out
    if not q.lines or any(l.sku_id is None for l in q.lines):
        raise HTTPException(status_code=409, detail="choose a product from the shortlist before pricing")
    return [l.sku_id for l in q.lines]


def _choose(ctx: Context, q, product_id: str, user: User):
    """Put the chosen product on the quote through QuoteService.shortlist: eligible and on time, re-checked by the
    rules now (a new version)."""
    if ctx.rules is None:
        raise HTTPException(status_code=503, detail="the rules are not configured in this app")
    p = ctx.catalog.products([product_id])
    if not p:
        raise HTTPException(status_code=404, detail=f"product {product_id} not found")
    (elig,), _ = ctx.rules.eligibility(p, q.requirements)
    ful, _ = ctx.rules.fulfilment(p, q.lines, q.requirements.delivery_days or 0)
    return ctx.service.shortlist(q.quote_id, q.revision, user.id, p[0], elig, ful[p[0].product_id])


def _key(key: str | None) -> str:
    if not key:
        raise HTTPException(status_code=428, detail="an Idempotency-Key header is required for this step")
    return key


@router.get("/{quote_id}")
def preview(quote_id: str, product_id: str | None = None, asked: float | None = None, user: User = Depends(rep),
            ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    skus = _skus(ctx, q, product_id)
    book, pack = as_rep(ctx, user).price_book(), ctx.catalog.packaging(skus)
    try:
        lines = [pricing.price_quote(sku, l.quantity, l.unit, _tier(ctx, q.account), book, pack)
                 | {"size": l.size} for sku, l in zip(skus, q.lines)]
    except pricing.PricingError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    out = {"quote_id": q.quote_id, "version": q.version, "revision": q.revision, "state": q.state.value,
           "product_id": product_id or q.product_id, "lines": lines,
           "total": round(sum(l["total"] for l in lines), 2)}
    if asked is not None:
        row = next(r for r in book if r.id == lines[0]["price_book_id"])
        out["band"] = pricing.discount_band(row.list_price, asked, row, ctx.service.policy.pricing)
        out["asked_total"] = round(asked * sum(l["boxes"] for l in lines), 2)
    return out


@router.post("/{quote_id}")
def price(quote_id: str, body: PriceIn, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    if q.revision != body.revision:
        raise HTTPException(status_code=409, detail="quote changed; reload")
    if body.product_id and body.product_id != q.product_id:
        q = _choose(ctx, q, body.product_id, user)
    q = ctx.service.price(quote_id, q.revision, user.id, _tier(ctx, q.account), as_rep(ctx, user).price_book(),
                          asked_unit_price=body.asked_unit_price, packaging=_packaging(ctx, q))
    return view(ctx, q)


@router.post("/{quote_id}/submit")
def submit(quote_id: str, body: StepIn, user: User = Depends(rep), ctx: Context = Depends(context),
           idempotency_key: str | None = Header(default=None)):
    _own(ctx, quote_id, user)
    return view(ctx, ctx.service.submit(quote_id, body.revision, user.id, key=_key(idempotency_key)))


@router.post("/{quote_id}/issue")
def issue(quote_id: str, body: StepIn, user: User = Depends(rep), ctx: Context = Depends(context),
          idempotency_key: str | None = Header(default=None)):
    q = _own(ctx, quote_id, user)
    if ctx.inputs_as_of is None:
        raise HTTPException(status_code=503, detail="the inputs' freshness is not configured in this app")
    q = ctx.service.issue(quote_id, body.revision, user.id, key=_key(idempotency_key),
                          inputs_as_of=ctx.inputs_as_of(q))
    return view(ctx, q)


@router.post("/{quote_id}/revise")
def revise(quote_id: str, body: ReviseIn, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    product = None
    if body.product_id:
        if ctx.rules is None:
            raise HTTPException(status_code=503, detail="the rules are not configured in this app")
        p = ctx.catalog.products([body.product_id])
        if not p:
            raise HTTPException(status_code=404, detail=f"product {body.product_id} not found")
        days = body.delivery_days if body.delivery_days is not None else q.requirements.delivery_days
        req = q.requirements.model_copy(update={"delivery_days": days})
        (elig,), _ = ctx.rules.eligibility(p, req)
        ful, _ = ctx.rules.fulfilment(p, q.lines, days or 0)
        product = (p[0], elig, ful[p[0].product_id])
    pack = ctx.catalog.packaging([s.sku_id for s in (product[0].skus if product else [])]
                                 + [l.sku_id for l in q.lines if l.sku_id])
    q = ctx.service.revise_terms(quote_id, body.revision, user.id, _tier(ctx, q.account), as_rep(ctx, user).price_book(),
                                 delivery_days=body.delivery_days, product=product,
                                 asked_unit_price=body.asked_unit_price, packaging=pack)
    return view(ctx, q)
