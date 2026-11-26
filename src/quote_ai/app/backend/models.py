"""Request bodies of the API. Every write names the revision the screen showed: optimistic locking end to end."""
from __future__ import annotations

from pydantic import BaseModel, Field

from quote_ai.domain.models import QuoteState


class NewRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    account: str = Field(min_length=1, max_length=200)


class Confirm(BaseModel):
    revision: int
    quantity_by_size: dict[str, int]
    unit: str
    gloves_per_unit: int
    from_memory: dict[str, bool | str] = Field(default_factory=dict)   # AI-20: remembered facts the rep confirmed


class QuickQuote(BaseModel):
    revision: int


class StateChange(BaseModel):
    revision: int
    to: QuoteState
