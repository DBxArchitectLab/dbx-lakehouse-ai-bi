"""/api/ops (AI-26): the Ops page, for the sales managers only.

  GET /api/ops[?quote_id=Q-...]   freshness per source and input with its limit, the SLOs per day (latency, live
                                  quality, issues refused on stale inputs, cost per quote), cost per quote by step
                                  including the app's own compute, a green or red status; with quote_id, that quote's
                                  cost by step (the capstone's "open the hood")

It reads the same quote_ai_monitoring objects as the operating dashboard (quote_ai.ops.monitoring.OPS_OBJECTS), so the
Ops page and the platform view can never disagree. 403 for anyone outside DBxLab_Sales_Managers: the menu hides the
page by role and the API checks again. No PII: scores, counts and costs only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from quote_ai.rules import approval as approval_rules

from ..auth import User, rep
from ..context import Context
from ..deps import context
from .approvals import groups_of

router = APIRouter(prefix="/api/ops", tags=["ops"])


@router.get("")
def ops(quote_id: str | None = Query(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$"), user: User = Depends(rep),
        ctx: Context = Depends(context)):
    if not approval_rules.is_approver(groups_of(ctx, user)):
        raise HTTPException(status_code=403, detail="the Ops page is for DBxLab_Sales_Managers")
    if ctx.ops is None:
        raise HTTPException(status_code=503, detail="monitoring is not configured in this app")
    return ctx.ops.view(quote_id)
