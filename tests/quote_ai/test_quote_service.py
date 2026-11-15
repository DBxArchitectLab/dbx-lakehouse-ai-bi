"""QuoteService on the whole Lakeside transaction (plan 4.6), and the ways it must refuse."""
import threading
from datetime import timedelta

import pytest

from quote_ai.domain.models import QuoteState as S
from quote_ai.rules import eligibility, fulfilment
from quote_ai.services import QuoteError, QuoteService
from quote_ai.storage.repository import InMemoryQuoteRepository, StaleRevision
import lakeside_fixtures as fx

MANAGER = {"sales_manager"}


@pytest.fixture
def svc():
    return QuoteService(InMemoryQuoteRepository(), clock=lambda: fx.NOW)


def checks(q, letter, snapshot="ev-41"):
    p = fx.product(letter)
    elig = eligibility.check_product(p, q.requirements, fx.evidence(snapshot), snapshot)
    ful = fulfilment.check_product(p, q.lines, fx.stock(), q.requirements.delivery_days, "inv-2026-11-14")
    return p, elig, ful


def to_priced(svc, letter="A", asked=8.00):
    q = svc.create("Lakeside Regional Clinic", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-1")
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, letter))
    return svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=asked)


def test_the_lakeside_transaction_end_to_end(svc):
    q = svc.create("Lakeside Regional Clinic", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-1")
    assert q.state == S.DRAFT and q.requirements.missing == ["quantity_per_size", "gloves_per_unit"]

    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    assert q.requirements.version == 2 and [l.quantity for l in q.lines] == [60, 140, 140, 60]

    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))
    assert q.evidence_snapshot == "ev-41" and all(l.sku_id.startswith("GLOVE-A") for l in q.lines)

    q = svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=8.00)
    assert q.discount == pytest.approx(0.0698, abs=1e-4) and q.approval_needed == "MANAGER"

    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="submit-1")
    assert q.state == S.PENDING_APPROVAL
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGER, key="approve-1")
    q = svc.issue(q.quote_id, q.revision, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    assert q.state == S.ISSUED

    trail = [e.action for e in svc.repo.events("Q-1")]
    assert trail == ["create", "confirm", "shortlist", "price", "submit", "approve", "issue"]


def test_lines_must_add_up_to_the_request(svc):
    q = svc.create("Lakeside", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1())
    with pytest.raises(QuoteError, match="add up to 390"):
        svc.confirm(q.quote_id, q.revision, "rep.alice", {"S": 60, "M": 130, "L": 140, "XL": 60}, "box", 100)


def test_a_glove_that_needs_evidence_cannot_be_shortlisted(svc):
    q = svc.create("Lakeside", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1())
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    with pytest.raises(QuoteError, match="needs_evidence"):
        svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "B", "ev-41"))
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "B", "ev-57"))   # after its report
    assert q.product_id == "GLOVE-B"


def test_a_late_glove_is_an_alternative_until_the_customer_accepts(svc):
    q = svc.create("Lakeside", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1())
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    with pytest.raises(QuoteError, match="acceptance"):
        svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "C"))
    v = q.version
    q = svc.accept_alternative(q.quote_id, q.revision, "rep.alice", delivery_days=21)
    assert q.requirements.version == 3 and q.version == v + 1
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "C"))
    assert q.product_id == "GLOVE-C"


def test_a_new_version_voids_the_approval(svc):
    q = to_priced(svc)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGER, key="a")
    q = svc.accept_alternative(q.quote_id, q.revision, "rep.alice", delivery_days=21)
    assert q.approval is None and q.state == S.REQUIREMENTS_CONFIRMED


def test_no_self_approval_and_only_managers(svc):
    q = to_priced(svc)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    with pytest.raises(QuoteError, match="self-approval"):
        svc.approve(q.quote_id, q.revision, "rep.alice", MANAGER, key="a1")
    with pytest.raises(QuoteError, match="not a sales manager"):
        svc.approve(q.quote_id, q.revision, "rep.bob", {"sales_rep"}, key="a2")


def test_two_managers_approve_at_once_one_gets_409(svc):
    q = to_priced(svc)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    results, start = [], threading.Barrier(2)

    def approve(who):
        start.wait()
        try:
            svc.approve(q.quote_id, q.revision, who, MANAGER, key=f"approve-{who}")
            results.append("ok")
        except StaleRevision:
            results.append("409")

    threads = [threading.Thread(target=approve, args=(m,)) for m in ("mgr.maria", "mgr.omar")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["409", "ok"]
    assert [e.action for e in svc.repo.events(q.quote_id)].count("approve") == 1


def test_issue_twice_is_one_issue(svc):
    q = to_priced(svc, asked=8.40)                      # within the rep's 5%: approved by rule
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    assert q.state == S.APPROVED
    rev = q.revision
    svc.issue(q.quote_id, rev, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    again = svc.issue(q.quote_id, rev, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    assert again.state == S.ISSUED
    assert [e.action for e in svc.repo.events(q.quote_id)].count("issue") == 1


def test_stale_inputs_never_issue(svc):
    q = to_priced(svc, asked=8.40)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    with pytest.raises(QuoteError, match="stale inputs: inventory"):
        svc.issue(q.quote_id, q.revision, "rep.alice", key="i",
                  inputs_as_of=fx.fresh_inputs() | {"inventory": fx.NOW - timedelta(hours=30)})


def test_a_price_below_the_margin_floor_is_refused(svc):
    with pytest.raises(QuoteError, match="refused"):
        to_priced(svc, asked=7.20)


def test_issue_without_approval_is_refused(svc):
    q = to_priced(svc)
    with pytest.raises(QuoteError, match="no valid approval"):
        svc.issue(q.quote_id, q.revision, "rep.alice", key="i", inputs_as_of=fx.fresh_inputs())
