"""/api/win-chance (AI-14): the win chance of one quote at several candidate prices, before the rep chooses one.

  POST /api/win-chance   {"quote_id": "...", "prices": [7.60, 7.90, 8.20]}
                         -> per price: the win chance, the price against list, whether it needs approval; the label
                            "simulation, demo data"; and whether the chance falls as the price rises (it must)

One request to the win model's serving endpoint for all the prices (quote_ai.ml.score: the rows are built by the same
request_features as training; the endpoint looks up the account and SKU features). Read-only: nothing on the quote
changes. The prediction informs the rep; it never sets the price, which still comes from the price book and the
policy (quote_ai.rules.pricing, through QuoteService).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from quote_ai.ml import score

from ..auth import User, rep
from ..context import Context
from ..deps import context
from .quotes import _own

router = APIRouter(prefix="/api/win-chance", tags=["win-chance"])


class WinChanceRequest(BaseModel):
    quote_id: str
    prices: list[float] = Field(min_length=1, max_length=5)


@router.post("")
def win_chance(body: WinChanceRequest, user: User = Depends(rep), ctx: Context = Depends(context)):
    q = _own(ctx, body.quote_id, user)
    if ctx.win_model is None:
        raise HTTPException(status_code=503, detail="no win model endpoint configured (QUOTE_AI_WIN_ENDPOINT)")
    if any(p <= 0 for p in body.prices):
        raise HTTPException(status_code=422, detail="every price must be above zero")
    try:
        rows = score.what_if_rows(q.quote_id, q.account, [l.model_dump() for l in q.lines], sorted(set(body.prices)))
        chances = score.win_chances(rows, ctx.win_model)
    except score.ScoringError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    problems = score.check_monotonic([(c["price"], c["win_chance"]) for c in chances])
    return {"quote_id": q.quote_id, "account": q.account, "sku_id": rows[0]["sku_id"],
            "quantity_boxes": rows[0]["quantity_boxes"], "list_price": rows[0]["list_price"], "prices": chances,
            "consistent": not problems, "problems": problems, "label": score.LABEL, "note": score.NOTE,
            "model": "win_model@champion"}
