"""Where quotes live, behind one interface: in memory for tests, Delta from AI-07 (storage/delta_repository.py),
Lakebase from AI-20.

The swap in AI-20 changes the implementation, not QuoteService.
"""
from __future__ import annotations

import threading
from typing import Protocol

from quote_ai.domain.models import Quote, QuoteEvent


class StaleRevision(RuntimeError):
    """Optimistic locking: the quote changed since the caller read it (HTTP 409)."""


class QuoteNotFound(KeyError):
    pass


class QuoteRepository(Protocol):
    def get(self, quote_id: str) -> Quote: ...
    def insert(self, quote: Quote, event: QuoteEvent) -> None: ...
    def update(self, quote: Quote, expected_revision: int, event: QuoteEvent, key: str | None = None) -> None: ...
    def events(self, quote_id: str) -> list[QuoteEvent]: ...
    def list_quotes(self, rep: str | None = None) -> list[Quote]: ...
    def idempotent_result(self, key: str) -> str | None: ...
    def remember(self, key: str, quote_id: str) -> None: ...


class InMemoryQuoteRepository:
    """Atomic like the real stores: the quote, its event and the idempotency key are written together."""

    def __init__(self) -> None:
        self._quotes: dict[str, Quote] = {}
        self._events: list[QuoteEvent] = []
        self._keys: dict[str, str] = {}
        self._lock = threading.Lock()

    def get(self, quote_id: str) -> Quote:
        try:
            return self._quotes[quote_id].model_copy(deep=True)
        except KeyError:
            raise QuoteNotFound(quote_id) from None

    def insert(self, quote: Quote, event: QuoteEvent) -> None:
        with self._lock:
            if quote.quote_id in self._quotes:
                raise StaleRevision(f"{quote.quote_id} exists")
            self._quotes[quote.quote_id] = quote.model_copy(deep=True)
            self._events.append(event)

    def update(self, quote: Quote, expected_revision: int, event: QuoteEvent, key: str | None = None) -> None:
        with self._lock:
            current = self._quotes.get(quote.quote_id)
            if current is None:
                raise QuoteNotFound(quote.quote_id)
            if current.revision != expected_revision:
                raise StaleRevision(f"{quote.quote_id}: revision {current.revision}, expected {expected_revision}")
            if key is not None and key in self._keys:
                raise StaleRevision(f"idempotency key {key} already used")
            self._quotes[quote.quote_id] = quote.model_copy(deep=True)
            self._events.append(event)
            if key is not None:
                self._keys[key] = quote.quote_id

    def events(self, quote_id: str) -> list[QuoteEvent]:
        return [e for e in self._events if e.quote_id == quote_id]

    def list_quotes(self, rep: str | None = None) -> list[Quote]:
        """The quotes (of one rep), the most recently changed first."""
        last = {e.quote_id: i for i, e in enumerate(self._events)}
        qs = [q for q in self._quotes.values() if rep is None or q.rep == rep]
        return [q.model_copy(deep=True) for q in sorted(qs, key=lambda q: -last.get(q.quote_id, -1))]

    def idempotent_result(self, key: str) -> str | None:
        return self._keys.get(key)

    def remember(self, key: str, quote_id: str) -> None:
        with self._lock:
            self._keys[key] = quote_id
