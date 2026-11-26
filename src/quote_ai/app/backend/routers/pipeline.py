"""/api/pipeline (AI-10): where the My pipeline page finds the sales overview dashboard to embed.

The app adds no query of its own: the page embeds the published AI/BI dashboard, which reads the metric views with
the viewer's own credentials. This route only says which dashboard, and who is signed in (for the rep filter).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from quote_ai.semantic.dashboard import rep_id_for

from ..auth import User, rep
from ..context import Context
from ..deps import context

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])


@router.get("")
def pipeline(user: User = Depends(rep), ctx: Context = Depends(context)):
    info = ctx.dashboard() if ctx.dashboard else {"url": None, "reason": "no dashboard configured"}
    return info | {"user": user.id, "rep": rep_id_for(user.id)}
