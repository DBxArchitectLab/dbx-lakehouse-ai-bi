"""/api/feedback (AI-22): thumbs up or down, and an optional comment, under every answer.

  POST /api/feedback   {"trace_id", "thumbs_up", "comment"?, "quote_id"?, "surface"?} -> {"saved", "on_trace",
                       "candidate_id"}

The rep is the signed-in user Databricks Apps forwards (X-Forwarded-Email), never a field in the body. The rating sits
on the trace of the exact answer (prompt, tool calls, output); a thumbs down also creates a candidate case in the
eval schema. A person writes its expected result before it joins the evaluation package, in a pull request.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import User, rep
from ..context import Context
from ..deps import context

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class Feedback(BaseModel):
    trace_id: str | None = Field(default=None, max_length=200)
    thumbs_up: bool
    comment: str | None = Field(default=None, max_length=2000)
    quote_id: str | None = None
    surface: Literal["chat", "compare", "ask_data"] = "chat"


@router.post("")
def feedback(f: Feedback, user: User = Depends(rep), ctx: Context = Depends(context)):
    if ctx.feedback is None:
        raise HTTPException(status_code=503, detail="feedback is not configured in this app")
    if not f.trace_id and not f.thumbs_up and not f.comment:
        raise HTTPException(status_code=422, detail="a thumbs down without a trace needs a comment")
    return ctx.feedback.rate(f.trace_id, user.id, f.thumbs_up, f.comment, f.quote_id, f.surface)
