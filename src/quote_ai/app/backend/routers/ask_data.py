"""/api/ask-data (AI-11): a question in plain language to the DBxLab sales analyst (a Genie Agent).

  POST /api/ask-data   {"question": "..."} -> the answer text, the SQL Genie wrote, and the result table

Identity (AI-24): Genie runs AS THE SIGNED-IN REP, with their forwarded token (scope dashboards.genie), so the row
filter on the sales tables and the cost mask apply: rep B asking what Lakeside pays gets no rows, and nobody but a
manager sees cost or margin. Until AI-24 it asked as the app's service principal, and that path had no filter.
Genie answers data questions; it does not decide eligibility or price a quote.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..auth import User, rep
from ..context import Context
from ..deps import as_rep, context

router = APIRouter(prefix="/api/ask-data", tags=["ask-data"])


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=1000)


@router.post("")
def ask_data(q: Question, user: User = Depends(rep), ctx: Context = Depends(context)):
    data = as_rep(ctx, user)
    try:
        answer = data.ask_data(q.question)
    except LookupError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=f"Genie did not answer in time: {e}") from e
    return answer | {"question": q.question, "asked_as": data.asked_as}
