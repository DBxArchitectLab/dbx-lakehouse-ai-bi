"""Human approval bound to one quote version (AI-21): the terms hash, voiding by a new version, the approver rules."""
import pytest

import lakeside_fixtures as fx
from agent_fixtures import priced
from quote_ai.domain.models import QuoteState as S
from quote_ai.rules import approval
from quote_ai.services import QuoteError
from quote_ai.services.quote_service import ApprovalRefused

MANAGERS = {"DBxLab_Sales_Managers"}


def pending(asked=8.00):
    svc, q = priced(asked=asked)
    return svc, svc.submit(q.quote_id, q.revision, "rep.alice", key="submit-1")


def test_approve_v3_then_can_issue():
    svc, q = pending()
    assert q.state == S.PENDING_APPROVAL and q.approval_needed == "MANAGER"
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1", expected_version=q.version)
    assert approval.can_issue(q, q.approval) and q.approval.terms_hash == approval.terms_hash(q)


def test_a_price_edit_makes_a_new_version_and_voids_the_approval():
    svc, q = pending()
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1")
    v = q.version
    q2 = svc.revise_terms(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=7.90)
    assert q2.version == v + 1 and q2.approval is None and q2.state == S.PENDING_APPROVAL
    assert not approval.can_issue(q2, q.approval)                  # v3's approval doesn't match v4
    with pytest.raises(QuoteError, match="no valid approval"):
        svc.issue(q2.quote_id, q2.revision, "rep.alice", key="i1", inputs_as_of=fx.fresh_inputs())


def test_a_later_date_the_customer_accepts_is_r3_and_a_new_version():
    svc, q = pending()
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1")
    r, v = q.requirements.version, q.version
    q4 = svc.revise_terms(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), delivery_days=21)
    assert (q4.requirements.version, q4.version, q4.state) == (r + 1, v + 1, S.PENDING_APPROVAL)
    q5 = svc.revise_terms(q4.quote_id, q4.revision, "rep.alice", "standard", fx.price_book(), delivery_days=10)
    assert q5.version == v + 2 and q5.state == S.PENDING_APPROVAL       # back to ten days: its own approval
    q5 = svc.approve(q5.quote_id, q5.revision, "mgr.omar", MANAGERS, key="a5", expected_version=q5.version)
    assert approval.can_issue(q5, q5.approval)


def test_switching_to_glove_c_needs_its_date_and_its_own_approval():
    from test_quote_service import checks
    svc, q = pending()
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1")
    p, elig, ful = checks(q, "C")
    with pytest.raises(QuoteError, match="misses the delivery date"):
        svc.revise_terms(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), product=(p, elig, ful))
    q = svc.revise_terms(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), delivery_days=21,
                         product=(p, elig, ful))
    assert q.product_id == "GLOVE-C" and q.state == S.PENDING_APPROVAL and q.requirements.delivery_days == 21


def test_no_self_approval_and_only_managers():
    svc, q = pending()
    with pytest.raises(ApprovalRefused, match="no self-approval"):
        svc.approve(q.quote_id, q.revision, "rep.alice", MANAGERS, key="x1")
    with pytest.raises(ApprovalRefused, match="not a sales manager"):
        svc.approve(q.quote_id, q.revision, "rep.bob", {"DBxLab_Sales_Reps"}, key="x2")
    assert svc.repo.get(q.quote_id).state == S.PENDING_APPROVAL


def test_the_rules_have_no_writes_and_the_hash_covers_the_terms():
    _, q = pending()
    h = approval.terms_hash(q)
    for change in ({"discount": 0.05}, {"evidence_snapshot": "ev-57"}, {"version": q.version + 1}):
        assert approval.terms_hash(q.model_copy(update=change)) != h
    q2 = q.model_copy(deep=True)
    q2.lines[0].unit_price = 7.99
    assert approval.terms_hash(q2) != h
    src = approval.__file__
    assert "repo" not in open(src, encoding="utf-8").read()


def test_within_five_percent_is_approved_by_rule_and_above_fifteen_is_refused():
    svc, q = priced(asked=8.30)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s")
    assert q.state == S.APPROVED and q.approval.approver == "policy"
    with pytest.raises(QuoteError, match="refused by the pricing policy"):
        priced(asked=6.88)                                        # 20% off: no approval button
