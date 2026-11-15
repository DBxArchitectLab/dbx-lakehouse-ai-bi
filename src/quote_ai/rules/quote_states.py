"""The quote state machine (plan 4.2): defined once, for the whole series. Code, not a model, decides a move."""
from __future__ import annotations

from quote_ai.domain.models import QuoteState as S


class InvalidTransition(ValueError):
    pass


ALLOWED: dict[S, set[S]] = {
    S.DRAFT: {S.REQUIREMENTS_CONFIRMED, S.CANCELLED},
    S.REQUIREMENTS_CONFIRMED: {S.SHORTLISTED, S.DRAFT, S.CANCELLED},
    S.SHORTLISTED: {S.PRICED, S.REQUIREMENTS_CONFIRMED, S.CANCELLED},
    S.PRICED: {S.PENDING_APPROVAL, S.APPROVED, S.SHORTLISTED, S.REQUIREMENTS_CONFIRMED, S.CANCELLED},
    S.PENDING_APPROVAL: {S.APPROVED, S.REJECTED, S.PRICED, S.REQUIREMENTS_CONFIRMED},
    S.APPROVED: {S.ISSUED, S.PRICED, S.REQUIREMENTS_CONFIRMED, S.EXPIRED},
    S.ISSUED: {S.EXPIRED},
    S.REJECTED: {S.PRICED, S.CANCELLED},
    S.EXPIRED: set(),
    S.CANCELLED: set(),
}


def check_transition(current: S, target: S) -> None:
    if target not in ALLOWED[current]:
        raise InvalidTransition(f"{current.value} -> {target.value} is not allowed")
