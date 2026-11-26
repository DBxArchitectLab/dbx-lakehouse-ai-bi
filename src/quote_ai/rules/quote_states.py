"""The quote state machine (plan 4.2): defined once, for the whole series. Code, not a model, decides a move.
From AI-20 the same table runs on Lakebase (only the storage changed); AI-21 adds APPROVED -> PENDING_APPROVAL for a
changed version.

Each state has one meaning; ALLOWED lists the moves that may follow it. Any move not in the table is refused, and
QuoteService is the only caller that commits one (rules/... decide, services/quote_service.py commits).

  DRAFT                   a request was read (requirements r1, with what is missing)
  REQUIREMENTS_CONFIRMED  the rep answered what was missing and confirmed (r2): one quote line per size
  SHORTLISTED             a product is on the quote, one SKU per line
  PRICED                  every line has a price
  PENDING_APPROVAL        the discount is outside the rep's limit: a manager decides
  APPROVED                within policy, or approved by a manager, for this quote version
  ISSUED                  sent to the customer, on fresh inputs
  REJECTED, EXPIRED, CANCELLED
"""
from __future__ import annotations

from quote_ai.domain.models import QuoteState as S

__all__ = ["S", "ALLOWED", "TERMINAL", "InvalidTransition", "transition", "check_transition"]


class InvalidTransition(ValueError):
    """The move is not in the ALLOWED table (HTTP 409 in the app)."""


ALLOWED: dict[S, set[S]] = {
    S.DRAFT: {S.REQUIREMENTS_CONFIRMED, S.CANCELLED},
    S.REQUIREMENTS_CONFIRMED: {S.SHORTLISTED, S.DRAFT, S.CANCELLED},
    S.SHORTLISTED: {S.PRICED, S.REQUIREMENTS_CONFIRMED, S.CANCELLED},
    S.PRICED: {S.PENDING_APPROVAL, S.APPROVED, S.SHORTLISTED, S.REQUIREMENTS_CONFIRMED, S.CANCELLED},  # APPROVED: within policy only
    S.PENDING_APPROVAL: {S.APPROVED, S.REJECTED, S.PRICED, S.REQUIREMENTS_CONFIRMED},
    # PRICED / REQUIREMENTS_CONFIRMED / PENDING_APPROVAL: a new version (AI-21: a changed version needs its own
    # approval, so an approved quote whose terms change goes back to PENDING_APPROVAL when the discount needs one)
    S.APPROVED: {S.ISSUED, S.PRICED, S.REQUIREMENTS_CONFIRMED, S.PENDING_APPROVAL, S.EXPIRED},
    S.ISSUED: {S.EXPIRED},
    S.REJECTED: {S.PRICED, S.CANCELLED},
    S.EXPIRED: set(),
    S.CANCELLED: set(),
}
TERMINAL = {s for s, nxt in ALLOWED.items() if not nxt}


def transition(current: S, target: S, within_policy: bool = False) -> S:
    """The target, if the move is allowed; InvalidTransition with the reason if not. PRICED -> APPROVED skips the
    manager, so it is allowed only when the price is within policy."""
    if target not in ALLOWED[current]:
        raise InvalidTransition(f"{current.value} -> {target.value} is not allowed")
    if current == S.PRICED and target == S.APPROVED and not within_policy:
        raise InvalidTransition("PRICED -> APPROVED: outside policy, needs PENDING_APPROVAL")
    return target


def check_transition(current: S, target: S, within_policy: bool = True) -> None:
    """Kept for callers that only need the table check."""
    transition(current, target, within_policy)
