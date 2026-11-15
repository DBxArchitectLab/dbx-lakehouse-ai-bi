"""QuoteService: the only path to a change in a quote (plan 4.1, 4.2).

The FastAPI routes, the agent's tools and the tests all call it. The agent may propose a transition; only this
service commits one. Every commit checks the state machine, uses optimistic locking on the quote's revision, and
writes a quote_event in the same write. Submit, approve and issue take an idempotency key, so a retried call does
not act twice.
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import datetime, timezone

from quote_ai.domain.models import (Approval, Eligibility, EligibilityResult, FulfilmentResult, Product, Quote,
                                    QuoteEvent, QuoteLine, QuoteState as S, Requirements)
from quote_ai.rules import pricing
from quote_ai.rules.approval import terms_hash
from quote_ai.rules.freshness import stale_inputs
from quote_ai.rules.policy import Policy
from quote_ai.rules.quote_states import check_transition
from quote_ai.storage.repository import QuoteRepository, StaleRevision


class QuoteError(ValueError):
    """A rule refused the change; the message says which rule."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class QuoteService:
    def __init__(self, repo: QuoteRepository, policy: Policy | None = None,
                 clock: Callable[[], datetime] = _utcnow) -> None:
        self.repo = repo
        self.policy = policy or Policy()
        self.clock = clock

    # ------------------------------------------------------------ internals

    def _commit(self, q: Quote, to: S, actor: str, action: str, expected_revision: int,
                new_version: bool = False, key: str | None = None, **detail) -> Quote:
        before = self.repo.get(q.quote_id)
        if before.revision != expected_revision:
            raise StaleRevision(f"{q.quote_id}: revision {before.revision}, expected {expected_revision}")
        if to != before.state:
            check_transition(before.state, to)
        q.state = to
        q.revision = before.revision + 1
        if new_version:
            q.version = before.version + 1
            q.approval = None  # a new version voids any approval
        event = QuoteEvent(quote_id=q.quote_id, quote_version=q.version, revision=q.revision, action=action,
                           from_state=before.state, to_state=to, actor=actor, at=self.clock(), detail=detail)
        self.repo.update(q, expected_revision, event, key=key)
        return q

    def _replay(self, key: str | None, quote_id: str) -> Quote | None:
        """Idempotency: a key already used for this quote returns the quote as it is, without acting again."""
        if key and self.repo.idempotent_result(key) == quote_id:
            return self.repo.get(quote_id)
        return None

    # ------------------------------------------------------------ the workflow

    def create(self, account: str, rep: str, request_text: str, requirements: Requirements,
               quote_id: str | None = None) -> Quote:
        q = Quote(quote_id=quote_id or f"Q-{uuid.uuid4().hex[:8]}", account=account, rep=rep,
                  request_text=request_text, requirements=requirements)
        self.repo.insert(q, QuoteEvent(quote_id=q.quote_id, quote_version=1, revision=1, action="create",
                                       from_state=None, to_state=S.DRAFT, actor=rep, at=self.clock(),
                                       detail={"missing": requirements.missing}))
        return q

    def confirm(self, quote_id: str, expected_revision: int, actor: str, quantity_by_size: dict[str, int],
                unit: str, gloves_per_unit: int) -> Quote:
        """Clarify and confirm (AI-07): the rep fills in what was missing; one quote line per size."""
        q = self.repo.get(quote_id)
        r = q.requirements
        if set(quantity_by_size) != set(r.sizes):
            raise QuoteError(f"sizes {sorted(quantity_by_size)} do not match the request {r.sizes}")
        if r.quantity is not None and sum(quantity_by_size.values()) != r.quantity:
            raise QuoteError(f"lines add up to {sum(quantity_by_size.values())}, the request is {r.quantity}")
        if any(n <= 0 for n in quantity_by_size.values()):
            raise QuoteError("every size needs a positive quantity")
        q.requirements = r.model_copy(update={
            "version": r.version + 1, "quantity_by_size": dict(quantity_by_size), "unit": unit,
            "gloves_per_unit": gloves_per_unit, "missing": []})
        q.lines = [QuoteLine(line_no=i + 1, size=s, quantity=quantity_by_size[s], unit=unit)
                   for i, s in enumerate(r.sizes)]
        return self._commit(q, S.REQUIREMENTS_CONFIRMED, actor, "confirm", expected_revision, new_version=True,
                            requirements_version=q.requirements.version, lines=quantity_by_size)

    def shortlist(self, quote_id: str, expected_revision: int, actor: str, product: Product,
                  eligibility: EligibilityResult, fulfilment: FulfilmentResult) -> Quote:
        """Put a product on the quote. Only an eligible product, and only if it ships in time; a late product is an
        alternative that needs the customer's acceptance first (accept_alternative)."""
        q = self.repo.get(quote_id)
        if eligibility.product_id != product.product_id or fulfilment.product_id != product.product_id:
            raise QuoteError("eligibility and fulfilment must be for the chosen product")
        if eligibility.status != Eligibility.ELIGIBLE:
            raise QuoteError(f"{product.product_id} is {eligibility.status.value}")
        if not fulfilment.on_time:
            raise QuoteError(f"{product.product_id} misses the delivery date: an alternative that needs the "
                             f"customer's acceptance")
        for line in q.lines:
            line.sku_id = product.sku_for(line.size).sku_id
        q.product_id = product.product_id
        q.evidence_snapshot = eligibility.evidence_snapshot
        q.inventory_snapshot = fulfilment.inventory_snapshot
        return self._commit(q, S.SHORTLISTED, actor, "shortlist", expected_revision, new_version=True,
                            product_id=product.product_id, evidence_snapshot=q.evidence_snapshot)

    def accept_alternative(self, quote_id: str, expected_revision: int, actor: str, delivery_days: int) -> Quote:
        """The customer accepted a later date: requirements r(n+1), a new quote version, any approval void."""
        q = self.repo.get(quote_id)
        if q.state in (S.ISSUED, S.EXPIRED, S.CANCELLED):
            raise QuoteError(f"cannot change a quote in {q.state.value}")
        q.requirements = q.requirements.model_copy(update={"version": q.requirements.version + 1,
                                                           "delivery_days": delivery_days})
        q.product_id, q.discount, q.approval_needed = None, None, None
        for line in q.lines:
            line.sku_id, line.unit_price, line.list_price, line.price_book_id = None, None, None, None
        return self._commit(q, S.REQUIREMENTS_CONFIRMED, actor, "accept_alternative", expected_revision,
                            new_version=True, delivery_days=delivery_days)

    def price(self, quote_id: str, expected_revision: int, actor: str, tier: str,
              rows: list[pricing.PriceBookRow], asked_unit_price: float | None = None) -> Quote:
        """price_quote per line from the price book (AI-18); an asked price below list becomes a discount whose
        approval the policy decides (AI-21)."""
        q = self.repo.get(quote_id)
        worst = "NONE"
        for line in q.lines:
            row = pricing.price_quote(line.sku_id, line.quantity, tier, rows)
            unit_price = row.unit_price if asked_unit_price is None else asked_unit_price
            need = pricing.approval_required(row.list_price, unit_price, row, self.policy.pricing)
            if need == "REFUSED":
                raise QuoteError(f"{line.sku_id}: ${unit_price} is refused by the pricing policy")
            worst = "MANAGER" if need == "MANAGER" else worst
            line.unit_price, line.list_price, line.price_book_id = unit_price, row.list_price, row.id
        q.discount = round(1 - q.lines[0].unit_price / q.lines[0].list_price, 4)
        q.approval_needed = worst
        return self._commit(q, S.PRICED, actor, "price", expected_revision, new_version=True,
                            discount=q.discount, approval_needed=worst)

    def submit(self, quote_id: str, expected_revision: int, actor: str, key: str) -> Quote:
        """Within policy: APPROVED by rule. Over the rep's limit: PENDING_APPROVAL for a manager."""
        if (q := self._replay(key, quote_id)) is not None:
            return q
        q = self.repo.get(quote_id)
        if q.approval_needed == "NONE":
            q.approval = Approval(approver="policy", terms_hash=terms_hash(q), quote_version=q.version,
                                  at=self.clock())
            return self._commit(q, S.APPROVED, actor, "submit", expected_revision, key=key, by="rule")
        return self._commit(q, S.PENDING_APPROVAL, actor, "submit", expected_revision, key=key)

    def approve(self, quote_id: str, expected_revision: int, approver: str, approver_roles: set[str],
                key: str) -> Quote:
        if (q := self._replay(key, quote_id)) is not None:
            return q
        q = self.repo.get(quote_id)
        if "sales_manager" not in approver_roles:
            raise QuoteError(f"{approver} is not a sales manager")
        if approver == q.rep:
            raise QuoteError("no self-approval: the approver must not be the quote's rep")
        q.approval = Approval(approver=approver, terms_hash=terms_hash(q), quote_version=q.version, at=self.clock())
        return self._commit(q, S.APPROVED, approver, "approve", expected_revision, key=key,
                            quote_version=q.version, terms_hash=q.approval.terms_hash)

    def reject(self, quote_id: str, expected_revision: int, approver: str, reason: str) -> Quote:
        q = self.repo.get(quote_id)
        return self._commit(q, S.REJECTED, approver, "reject", expected_revision, reason=reason)

    def issue(self, quote_id: str, expected_revision: int, actor: str, key: str,
              inputs_as_of: dict[str, datetime]) -> Quote:
        """Issue only an approved version whose terms are unchanged, on fresh inputs."""
        if (q := self._replay(key, quote_id)) is not None:
            return q
        q = self.repo.get(quote_id)
        if q.approval is None or q.approval.quote_version != q.version or q.approval.terms_hash != terms_hash(q):
            raise QuoteError("no valid approval for this quote version")
        stale = stale_inputs(inputs_as_of, self.clock(), self.policy.freshness)
        if stale:
            raise QuoteError(f"stale inputs: {', '.join(stale)}; refresh before issuing")
        return self._commit(q, S.ISSUED, actor, "issue", expected_revision, key=key)
