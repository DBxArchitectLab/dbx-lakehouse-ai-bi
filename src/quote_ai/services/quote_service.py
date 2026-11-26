"""QuoteService: the only path to a change in a quote (plan 4.1, 4.2).

The FastAPI routes, the agent's tools and the tests all call it. The agent may propose a transition; only this
service commits one. Every change goes through one private method, _move: it asks the state machine (ALLOWED in
rules/quote_states.py) for the transition, saves the quote with optimistic locking on its revision, and writes one
quote_event in the same write. A refused move writes nothing. Submit, approve and issue take an idempotency key, so a
retried call does not act twice.

v1 (AI-07): read_request (extraction -> DRAFT), confirm (REQUIREMENTS_CONFIRMED), quick_quote (simple rules ->
SHORTLISTED -> PRICED) and mine. The later steps (shortlist on eligibility, price, submit, approve, issue) are here
already, tested on the Lakeside transaction, and reach the app in later episodes.

Season 5: price is price_quote's number for every line (AI-18); confirm_fact writes a customer fact the rep confirmed
and confirm takes requirements the rep confirmed from memory (AI-20); issue refuses stale inputs with StaleEvidence,
naming each one and its date (AI-20); approve is bound to the version the manager saw, checked by
rules/approval.py, and revise_terms makes a new version that needs its own approval (AI-21). On Lakebase (AI-20) the
quote, its lines, version, approval, event and idempotency key are one transaction.
"""
from __future__ import annotations

import contextlib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from quote_ai.domain.models import (Approval, Eligibility, EligibilityResult, FulfilmentResult, Product, Quote,
                                    QuoteEvent, QuoteLine, QuoteState as S, Requirements)
from quote_ai.rules import approval as approval_rules
from quote_ai.rules import pricing, simple_rules
from quote_ai.rules.approval import terms_hash
from quote_ai.rules.freshness import stale_details
from quote_ai.rules.policy import Policy
from quote_ai.rules.quote_states import InvalidTransition, transition
from quote_ai.storage.repository import QuoteRepository, StaleRevision


class QuoteError(ValueError):
    """A rule refused the change; the message says which rule."""


class StaleEvidence(QuoteError):
    """AI-20: an input behind the quote is older than the freshness policy allows; it may inform, never issue."""

    def __init__(self, stale: list[str]) -> None:
        super().__init__(f"stale inputs: {'; '.join(stale)}; refresh before issuing")
        self.stale = stale


class ApprovalRefused(QuoteError, approval_rules.PolicyError):
    """AI-21: self-approval, or an approver outside the managers' group (HTTP 403)."""


# AI-20: requirement fields a remembered customer fact may fill, once the rep confirms it on the quote
FROM_MEMORY = {"latex_free", "powder_free", "chemo_tested", "sterile", "material", "intended_use"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trace_id() -> str | None:
    """The MLflow trace of the current request, if tracing is on (verify the span attribute name)."""
    try:
        import mlflow
        span = mlflow.get_current_active_span()
        return getattr(span, "trace_id", None) or getattr(span, "request_id", None)
    except Exception:  # noqa: BLE001  no MLflow, or no active trace
        return None


def _span(name: str):
    try:
        import mlflow
        return mlflow.start_span(name)
    except Exception:  # noqa: BLE001
        return contextlib.nullcontext()


# Moves the generic state route may make: those that need no data of their own. Every other state is reached through
# its own step (confirm, shortlist, price, submit, approve, issue), which checks its own rules.
DIRECT_MOVES = {S.CANCELLED, S.EXPIRED}


class QuoteService:
    def __init__(self, repo: QuoteRepository, policy: Policy | None = None,
                 clock: Callable[[], datetime] = _utcnow, trace_id: Callable[[], str | None] = _trace_id,
                 memory: Any = None) -> None:
        self.repo = repo
        self.policy = policy or Policy()
        self.clock = clock
        self.trace_id = trace_id
        self.memory = memory            # AI-20: quote_ai.agents.memory.Memory

    # ------------------------------------------------------------ the one door

    def _move(self, q: Quote, to: S, actor: str, action: str, expected_revision: int,
              new_version: bool = False, key: str | None = None, within_policy: bool = False, **detail) -> Quote:
        """Ask the ALLOWED table, save, write one quote_event. Raises before anything is written if the move is
        refused (InvalidTransition) or the quote changed since the caller read it (StaleRevision)."""
        before = self.repo.get(q.quote_id)
        if before.revision != expected_revision:
            raise StaleRevision(f"quote changed; reload ({q.quote_id}: revision {before.revision}, "
                                f"expected {expected_revision})")
        if to != before.state:
            transition(before.state, to, within_policy=within_policy)
        q.state = to
        q.revision = before.revision + 1
        if new_version:
            q.version = before.version + 1
            q.approval = None  # a new version voids any approval
        event = QuoteEvent(quote_id=q.quote_id, quote_version=q.version, revision=q.revision, action=action,
                           from_state=before.state, to_state=to, actor=actor, at=self.clock(), detail=detail,
                           trace_id=self.trace_id(), idempotency_key=key)
        self.repo.update(q, expected_revision, event, key=key)
        return q

    @staticmethod
    def _only_from(q: Quote, state: S, step: str) -> None:
        """A step that acts once: a new key on a quote already past it is refused, never a second event."""
        if q.state != state:
            raise InvalidTransition(f"{q.state.value} -> {step}: only a {state.value} quote")

    def _replay(self, key: str | None, quote_id: str) -> Quote | None:
        """Idempotency: a key already used for this quote returns the quote as it is, without acting again."""
        if key and self.repo.idempotent_result(key) == quote_id:
            return self.repo.get(quote_id)
        return None

    # ------------------------------------------------------------ the workflow

    def create(self, account: str, rep: str, request_text: str, requirements: Requirements,
               quote_id: str | None = None, extracted: dict | None = None) -> Quote:
        q = Quote(quote_id=quote_id or f"Q-{uuid.uuid4().hex[:8]}", account=account, rep=rep,
                  request_text=request_text, requirements=requirements, extracted=extracted)
        self.repo.insert(q, QuoteEvent(quote_id=q.quote_id, quote_version=1, revision=1, action="create",
                                       from_state=None, to_state=S.DRAFT, actor=rep, at=self.clock(),
                                       detail={"missing": requirements.missing}, trace_id=self.trace_id()))
        return q

    def read_request(self, account: str, rep: str, text: str, extractor: Callable[[str], Any],
                     quote_id: str | None = None) -> Quote:
        """AI-07's first step: the request is read by the extraction (AI-05, through quote_ai_extract), and the
        quote starts in DRAFT with r1 and what is missing. `extractor` returns quote_ai.llm Requirements."""
        from quote_ai.llm.requirements_schema import to_domain
        with _span("read_request"):
            extracted = extractor(text)
            return self.create(account, rep, text, to_domain(extracted), quote_id=quote_id,
                               extracted=extracted.model_dump(mode="json"))

    def confirm(self, quote_id: str, expected_revision: int, actor: str, quantity_by_size: dict[str, int],
                unit: str, gloves_per_unit: int, from_memory: dict | None = None) -> Quote:
        """Clarify and confirm (AI-07): the rep fills in what was missing; one quote line per size. from_memory (AI-20):
        requirements the rep confirmed from the customer's remembered facts ("always latex-free"), now hard."""
        q = self.repo.get(quote_id)
        unknown = set(from_memory or {}) - FROM_MEMORY
        if unknown:
            raise QuoteError(f"not a requirement memory can fill: {sorted(unknown)}")
        r = q.requirements
        if set(quantity_by_size) != set(r.sizes):
            raise QuoteError(f"sizes {sorted(quantity_by_size)} do not match the request {r.sizes}")
        if r.quantity is not None and sum(quantity_by_size.values()) != r.quantity:
            raise QuoteError(f"lines add up to {sum(quantity_by_size.values())}, the request is {r.quantity}")
        if any(n <= 0 for n in quantity_by_size.values()):
            raise QuoteError("every size needs a positive quantity")
        if not unit or gloves_per_unit is None or gloves_per_unit <= 0:
            raise QuoteError("the selling unit and the gloves in one unit are needed")
        q.requirements = r.model_copy(update={
            "version": r.version + 1, "quantity_by_size": dict(quantity_by_size), "unit": unit,
            "gloves_per_unit": gloves_per_unit, "missing": []} | dict(from_memory or {}))
        q.lines = [QuoteLine(line_no=i + 1, size=s, quantity=quantity_by_size[s], unit=unit)
                   for i, s in enumerate(r.sizes)]
        return self._move(q, S.REQUIREMENTS_CONFIRMED, actor, "confirm", expected_revision, new_version=True,
                          requirements_version=q.requirements.version, lines=quantity_by_size,
                          **({"from_memory": from_memory} if from_memory else {}))

    def confirm_fact(self, account: str, fact_key: str, fact_value: str, rep: str, shared: bool = False) -> dict:
        """AI-20: the rep confirmed a customer fact the agent proposed ("Save for Lakeside: always latex-free?").
        The only way a fact reaches long-term memory: the agent proposes, the rep confirms, this writes."""
        if self.memory is None:
            raise QuoteError("memory is not configured")
        return self.memory.save_fact(account, fact_key, fact_value, owner_rep=rep, confirmed_by=rep, shared=shared)

    def quick_quote(self, quote_id: str, expected_revision: int, actor: str,
                    catalog: list[simple_rules.CatalogSku], rows: list[pricing.PriceBookRow]) -> Quote:
        """v1's simple rules (AI-07): an exact match on the SKU model -> SHORTLISTED, then the list price x the units
        on each line -> PRICED. Labelled v1; stops at PRICED. Replaced by shortlist (AI-15) and price (AI-18)."""
        q = self.repo.get(quote_id)
        if q.state != S.REQUIREMENTS_CONFIRMED:
            transition(q.state, S.SHORTLISTED)          # refuses with the reason, before anything is written
        matches = simple_rules.exact_match(q.requirements, q.lines, catalog)
        priced = []
        for m in matches:
            try:
                book = simple_rules.list_price(m.skus, q.lines, rows)
            except ValueError:
                continue
            priced.append((sum(r.list_price * l.quantity for r, l in zip(book, q.lines)), m.product_id, m, book))
        if not priced:
            raise QuoteError("no glove matches exactly with a verified SKU and a list price for every line")
        _, _, best, book = min(priced, key=lambda t: (t[0], t[1]))
        for line in q.lines:
            line.sku_id = best.skus[line.size]
        q.product_id, q.checks = best.product_id, best.checks
        q = self._move(q, S.SHORTLISTED, actor, "quick_quote_match", expected_revision, new_version=True,
                       method="v1_exact_match", product_id=best.product_id,
                       matches=[m.product_id for m in matches], checks=best.checks)
        for line, row in zip(q.lines, book):
            line.unit_price, line.list_price, line.price_book_id = row.list_price, row.list_price, row.id
        q.price_method, q.discount, q.approval_needed = simple_rules.PRICE_METHOD, None, None
        return self._move(q, S.PRICED, actor, "quick_quote_price", q.revision, new_version=True,
                          price_method=q.price_method, total=q.total, label=simple_rules.LABEL)

    def change_state(self, quote_id: str, expected_revision: int, actor: str, target: S) -> Quote:
        """A move asked for by state alone (the API's /state route). The ALLOWED table answers first, so a jump like
        REQUIREMENTS_CONFIRMED -> ISSUED is refused as not allowed; an allowed state with its own step is refused
        with the step's name."""
        q = self.repo.get(quote_id)
        if target == q.state:
            raise QuoteError(f"{quote_id} is already {target.value}")
        transition(q.state, target, within_policy=False)
        if target not in DIRECT_MOVES:
            raise QuoteError(f"{target.value} is reached through its own step, not set directly")
        return self._move(q, target, actor, target.value.lower(), expected_revision)

    def mine(self, rep: str) -> list[Quote]:
        return self.repo.list_quotes(rep=rep)

    def pending_approval(self) -> list[Quote]:
        """AI-21: the managers' inbox: every quote waiting for a manager."""
        if hasattr(self.repo, "pending_approval"):
            return self.repo.pending_approval()
        return [q for q in self.repo.list_quotes() if q.state == S.PENDING_APPROVAL]

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
        return self._move(q, S.SHORTLISTED, actor, "shortlist", expected_revision, new_version=True,
                          product_id=product.product_id, evidence_snapshot=q.evidence_snapshot)

    def shortlist_candidates(self, quote_id: str, expected_revision: int, actor: str,
                             results: list[EligibilityResult], fulfilment: dict[str, FulfilmentResult],
                             evidence_snapshot: str, inventory_snapshot: str) -> Quote:
        """AI-15: record what search and the rules found for this version and move to SHORTLISTED. Replaces v1's
        quick shortlist. Refuses, before anything is written: a result whose status doesn't follow from its checks
        (unknown is never eligible), an eligible product without its fulfilment answer, fulfilment for a product
        that isn't eligible, and a shortlist with no eligible product (the rep needs evidence or other options).
        A late product stays on the list as an alternative that needs the customer's acceptance."""
        from quote_ai.rules.eligibility import status_of
        q = self.repo.get(quote_id)
        if q.state not in (S.REQUIREMENTS_CONFIRMED, S.SHORTLISTED):
            transition(q.state, S.SHORTLISTED)       # refuses with the reason
        for r in results:
            if status_of(r.checks) != r.status:
                raise QuoteError(f"{r.product_id}: status {r.status.value} does not follow from its checks")
        eligible = {r.product_id for r in results if r.status == Eligibility.ELIGIBLE}
        if set(fulfilment) != eligible:
            raise QuoteError("fulfilment is decided for exactly the eligible products")
        if not eligible:
            raise QuoteError("no eligible glove: every candidate is excluded or needs evidence")
        q.candidates = [{"product_id": r.product_id, "status": r.status.value,
                         "failed": [c.requirement for c in r.checks if c.result.value != "verified_match"],
                         "on_time": fulfilment[r.product_id].on_time if r.product_id in fulfilment else None,
                         "fulfilment": fulfilment[r.product_id].status.value if r.product_id in fulfilment else None,
                         "label": fulfilment[r.product_id].label if r.product_id in fulfilment else None}
                        for r in results]
        q.evidence_snapshot, q.inventory_snapshot = evidence_snapshot, inventory_snapshot
        return self._move(q, S.SHORTLISTED, actor, "shortlist_candidates", expected_revision, new_version=True,
                          eligible=sorted(eligible),
                          on_time=sorted(p for p in eligible if fulfilment[p].on_time),
                          needs_evidence=[r.product_id for r in results if r.status == Eligibility.NEEDS_EVIDENCE],
                          excluded=[r.product_id for r in results if r.status == Eligibility.EXCLUDED],
                          evidence_snapshot=evidence_snapshot, inventory_snapshot=inventory_snapshot)

    def accept_alternative(self, quote_id: str, expected_revision: int, actor: str, delivery_days: int) -> Quote:
        """The customer accepted a later date: requirements r(n+1), a new quote version, any approval void."""
        q = self.repo.get(quote_id)
        if q.state in (S.ISSUED, S.EXPIRED, S.CANCELLED):
            raise QuoteError(f"cannot change a quote in {q.state.value}")
        q.requirements = q.requirements.model_copy(update={"version": q.requirements.version + 1,
                                                           "delivery_days": delivery_days})
        q.product_id, q.discount, q.approval_needed, q.price_method = None, None, None, None
        for line in q.lines:
            line.sku_id, line.unit_price, line.list_price, line.price_book_id = None, None, None, None
        return self._move(q, S.REQUIREMENTS_CONFIRMED, actor, "accept_alternative", expected_revision,
                          new_version=True, delivery_days=delivery_days)

    @staticmethod
    def packaging_from_requirements(q: Quote) -> dict[str, pricing.Packaging]:
        """The packaging the rep confirmed (r2: the selling unit and the gloves in one), per SKU on the quote. The app
        passes the catalog's packaging instead when it has it; neither is ever assumed."""
        r = q.requirements
        if not r.unit or not r.gloves_per_unit:
            raise QuoteError("the selling unit and the gloves in one unit are not confirmed")
        unit = pricing.normalize_unit(r.unit)
        return {l.sku_id: pricing.Packaging(selling_unit=unit, gloves_per={"each": 1, unit: r.gloves_per_unit})
                for l in q.lines if l.sku_id}

    def price(self, quote_id: str, expected_revision: int, actor: str, tier: str,
              rows: list[pricing.PriceBookRow], asked_unit_price: float | None = None,
              packaging: dict[str, pricing.Packaging] | None = None, on=None) -> Quote:
        """price_quote for every quote line, from the price book (AI-18): PRICED means price_quote's number. An asked
        price below list becomes a discount whose approval the policy decides (AI-21): REFUSED raises, nothing is
        written."""
        q = self.repo.get(quote_id)
        if any(l.sku_id is None for l in q.lines):
            raise QuoteError("every line needs a SKU before it is priced (shortlist first)")
        pack = packaging or self.packaging_from_requirements(q)
        by_id = {r.id: r for r in rows}
        worst = "NONE"
        for line in q.lines:
            try:
                p = pricing.price_quote(line.sku_id, line.quantity, line.unit, tier, rows, pack, on)
            except pricing.PricingError as e:
                raise QuoteError(f"price_quote: {e}") from e
            row = by_id[p["price_book_id"]]
            unit_price = p["unit_price"] if asked_unit_price is None else asked_unit_price
            need = pricing.approval_required(row.list_price, unit_price, row, self.policy.pricing)
            if need == "REFUSED":
                raise QuoteError(f"{line.sku_id}: ${unit_price} is refused by the pricing policy")
            worst = "MANAGER" if need == "MANAGER" else worst
            line.quantity, line.unit = p["boxes"], p["selling_unit"]
            line.unit_price, line.list_price, line.price_book_id = unit_price, row.list_price, row.id
        q.discount = round(1 - q.lines[0].unit_price / q.lines[0].list_price, 4)
        q.approval_needed = worst
        q.price_method = "price_quote"
        return self._move(q, S.PRICED, actor, "price", expected_revision, new_version=True,
                          discount=q.discount, approval_needed=worst,
                          price_book_ids=[l.price_book_id for l in q.lines])

    def submit(self, quote_id: str, expected_revision: int, actor: str, key: str) -> Quote:
        """Within policy: APPROVED by rule. Over the rep's limit: PENDING_APPROVAL for a manager."""
        if (q := self._replay(key, quote_id)) is not None:
            return q
        q = self.repo.get(quote_id)
        self._only_from(q, S.PRICED, "submit")
        if q.approval_needed == "NONE":
            q.approval = Approval(approver="policy", terms_hash=terms_hash(q), quote_version=q.version,
                                  at=self.clock())
            return self._move(q, S.APPROVED, actor, "submit", expected_revision, key=key, within_policy=True,
                              by="rule")
        if q.approval_needed is None:
            raise QuoteError("not priced by the pricing policy: v1 prices are not submitted for approval")
        return self._move(q, S.PENDING_APPROVAL, actor, "submit", expected_revision, key=key)

    def approve(self, quote_id: str, expected_revision: int, approver: str, approver_roles: set[str],
                key: str, expected_version: int | None = None) -> Quote:
        """A manager approves the version they saw (AI-21): the key first (a double click or a retry returns the first
        result), then the version (the quote changed since: StaleRevision, 409 "quote changed; reload"), then the
        approver rules; the approval with its terms hash, APPROVED and the quote_event are one write."""
        if (q := self._replay(key, quote_id)) is not None:
            return q
        q = self.repo.get(quote_id)
        if expected_version is not None and q.version != expected_version:
            raise StaleRevision(f"quote changed; reload ({quote_id} is version {q.version}, not {expected_version})")
        if q.state != S.PENDING_APPROVAL:            # decided since the manager's screen loaded it
            raise StaleRevision(f"quote changed; reload ({quote_id} is {q.state.value})")
        try:
            approval_rules.check_approver(q, approver, approver_roles)
        except approval_rules.PolicyError as e:
            raise ApprovalRefused(str(e)) from e
        q.approval = Approval(approver=approver, terms_hash=terms_hash(q), quote_version=q.version, at=self.clock())
        return self._move(q, S.APPROVED, approver, "approve", expected_revision, key=key,
                          quote_version=q.version, terms_hash=q.approval.terms_hash)

    def reject(self, quote_id: str, expected_revision: int, approver: str, reason: str) -> Quote:
        q = self.repo.get(quote_id)
        return self._move(q, S.REJECTED, approver, "reject", expected_revision, reason=reason)

    def issue(self, quote_id: str, expected_revision: int, actor: str, key: str,
              inputs_as_of: dict[str, datetime]) -> Quote:
        """Issue only an approved version whose terms are unchanged, on fresh inputs."""
        if (q := self._replay(key, quote_id)) is not None:
            return q
        q = self.repo.get(quote_id)
        if q.state == S.ISSUED:                      # a new key after the quote went out: never a second issue
            raise InvalidTransition(f"{quote_id} is already ISSUED")
        latest = self.repo.latest_approval(quote_id) if hasattr(self.repo, "latest_approval") else q.approval
        if not approval_rules.can_issue(q, latest) or not approval_rules.can_issue(q, q.approval):
            raise QuoteError("no valid approval for this quote version")
        stale = stale_details(inputs_as_of, self.clock(), self.policy.freshness)
        if stale:
            raise StaleEvidence(stale)
        return self._move(q, S.ISSUED, actor, "issue", expected_revision, key=key,
                          inputs_as_of={k: v.isoformat() for k, v in inputs_as_of.items()})

    def revise_terms(self, quote_id: str, expected_revision: int, actor: str, tier: str,
                     rows: list[pricing.PriceBookRow], delivery_days: int | None = None,
                     product: tuple[Product, EligibilityResult, FulfilmentResult] | None = None,
                     asked_unit_price: float | None = None,
                     packaging: dict[str, pricing.Packaging] | None = None) -> Quote:
        """AI-21: change the terms of a priced, pending or approved quote: a later delivery date the customer
        accepted (requirements r(n+1)), another eligible product that ships by that date, or another price. Always a
        new version, so any approval is void; the price is price_quote's again, and the policy decides: within the
        rep's limit PRICED (submit approves by rule), the manager's band PENDING_APPROVAL, above it refused."""
        q = self.repo.get(quote_id)
        if q.state not in (S.PRICED, S.PENDING_APPROVAL, S.APPROVED, S.REJECTED):
            raise QuoteError(f"terms are revised on a priced quote, not in {q.state.value}")
        if delivery_days is not None and delivery_days != q.requirements.delivery_days:
            q.requirements = q.requirements.model_copy(update={"version": q.requirements.version + 1,
                                                               "delivery_days": delivery_days})
        if product is not None:
            p, elig, ful = product
            if elig.product_id != p.product_id or ful.product_id != p.product_id:
                raise QuoteError("eligibility and fulfilment must be for the chosen product")
            if elig.status != Eligibility.ELIGIBLE:
                raise QuoteError(f"{p.product_id} is {elig.status.value}")
            if not all(l.ready_day <= (q.requirements.delivery_days or 0) for l in ful.lines):
                raise QuoteError(f"{p.product_id} misses the delivery date of requirements r{q.requirements.version}")
            for line in q.lines:
                line.sku_id = p.sku_for(line.size).sku_id
            q.product_id, q.evidence_snapshot = p.product_id, elig.evidence_snapshot
            q.inventory_snapshot = ful.inventory_snapshot
        asked = asked_unit_price if asked_unit_price is not None else (
            None if not q.discount else q.lines[0].unit_price)
        pack = packaging or self.packaging_from_requirements(q)
        by_id, worst = {r.id: r for r in rows}, "NONE"
        for line in q.lines:
            try:
                pq = pricing.price_quote(line.sku_id, line.quantity, line.unit, tier, rows, pack)
            except pricing.PricingError as e:
                raise QuoteError(f"price_quote: {e}") from e
            row = by_id[pq["price_book_id"]]
            unit_price = pq["unit_price"] if asked is None else asked
            need = pricing.approval_required(row.list_price, unit_price, row, self.policy.pricing)
            if need == "REFUSED":
                raise QuoteError(f"{line.sku_id}: ${unit_price} is refused by the pricing policy")
            worst = "MANAGER" if need == "MANAGER" else worst
            line.unit_price, line.list_price, line.price_book_id = unit_price, row.list_price, row.id
        q.discount = round(1 - q.lines[0].unit_price / q.lines[0].list_price, 4)
        q.approval_needed, q.price_method = worst, "price_quote"
        to = S.PENDING_APPROVAL if worst == "MANAGER" else S.PRICED
        return self._move(q, to, actor, "revise_terms", expected_revision, new_version=True,
                          requirements_version=q.requirements.version, delivery_days=q.requirements.delivery_days,
                          product_id=q.product_id, discount=q.discount, approval_needed=worst)
