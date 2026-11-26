"""/api/shortlist (AI-15): Find products on a confirmed quote. Search finds candidates, the rules decide, QuoteService
commits. The route decides nothing: it calls the shared package and returns what it said.

  POST /api/shortlist/{quote_id}   {"revision": n}
      the confirmed requirements as a query -> AI Search (hybrid, size and distribution as pre-filters only)
      -> the eligibility engine per candidate (quote_ai_ai.check_eligibility, or the same Python in a demo)
      -> fulfilment per eligible glove (quote_ai_ai.check_fulfilment)
      -> QuoteService.shortlist_candidates: REQUIREMENTS_CONFIRMED -> SHORTLISTED (a new quote version)
      Returns three groups: eligible (with the fulfilment column; a late glove labelled "alternative, needs
      acceptance"), needs evidence, excluded (with the check that failed); every check with its state and evidence.
      With no eligible glove the quote doesn't move: "moved": false and the reason.

Replaces v1's quick shortlist (AI-07), which was a plain filter on the SKU model; /api/quotes/{id}/quick-quote stays,
labelled v1, for the Season 3 Quote page until price_quote arrives in AI-18.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from quote_ai.services.quote_service import QuoteError
from quote_ai.services.shortlist import build_shortlist, view as shortlist_view

from ..auth import User, rep
from ..context import Context
from ..deps import context
from .quotes import _own, view

router = APIRouter(prefix="/api/shortlist", tags=["shortlist"])


class FindProducts(BaseModel):
    revision: int


@router.post("/{quote_id}")
def find_products(quote_id: str, body: FindProducts, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, quote_id, user)
    if ctx.sku_index is None or ctx.rules is None:
        raise HTTPException(status_code=503, detail="search is not configured in this app")
    if q.revision != body.revision:
        raise HTTPException(status_code=409, detail=f"{quote_id} changed since revision {body.revision}")
    s = build_shortlist(q.requirements, q.lines, ctx.sku_index, ctx.catalog, ctx.rules)
    moved, reason = True, ""
    try:
        q = ctx.service.shortlist_candidates(quote_id, body.revision, user.id, s.results, s.fulfilment,
                                             s.evidence_snapshot, s.inventory_snapshot)
    except QuoteError as e:
        if "no eligible glove" not in str(e):
            raise
        moved, reason = False, str(e)
    return {"quote": view(ctx, q), "shortlist": shortlist_view(s), "moved": moved, "reason": reason}
