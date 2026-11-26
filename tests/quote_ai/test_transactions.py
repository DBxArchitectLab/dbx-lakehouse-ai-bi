"""Transactional approval (AI-21): optimistic locking, idempotency keys and quote_event in one write, on the in-memory
store and on the Lakebase repository (SQLite running the same statements)."""
import threading

import pytest

import lakeside_fixtures as fx
from agent_fixtures import priced, service
from quote_ai.domain.models import QuoteState as S
from quote_ai.storage.lakebase import sqlite_db
from quote_ai.storage.lakebase_repository import LakebaseQuoteRepository
from quote_ai.storage.repository import InMemoryQuoteRepository, StaleRevision

MANAGERS = {"DBxLab_Sales_Managers"}


@pytest.fixture(params=["memory", "lakebase"])
def svc(request):
    return service(InMemoryQuoteRepository() if request.param == "memory" else LakebaseQuoteRepository(sqlite_db()))


def pending(svc):
    svc, q = priced(svc, asked=8.00)
    return svc.submit(q.quote_id, q.revision, "rep.alice", key="submit-1")


def test_two_approvals_of_v3_in_parallel_one_wins_one_conflict(svc):
    q = pending(svc)
    results, start = [], threading.Barrier(2)

    def approve(who):
        start.wait()
        try:
            svc.approve(q.quote_id, q.revision, who, MANAGERS, key=f"approve-{who}", expected_version=q.version)
            results.append("APPROVED")
        except StaleRevision:
            results.append("409")
    threads = [threading.Thread(target=approve, args=(m,)) for m in ("mgr.maria", "mgr.omar")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["409", "APPROVED"]
    assert [e.action for e in svc.repo.events(q.quote_id)].count("approve") == 1
    if hasattr(svc.repo, "approvals"):
        assert len(svc.repo.approvals(q.quote_id)) == 1          # exactly one approval per version and hash


def test_issue_twice_with_one_key_is_one_issued_and_one_event(svc):
    q = pending(svc)
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1")
    first = svc.issue(q.quote_id, q.revision, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    again = svc.issue(q.quote_id, q.revision, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    assert first.state == again.state == S.ISSUED and again.revision == first.revision
    assert [e.action for e in svc.repo.events(q.quote_id)].count("issue") == 1


def test_approve_with_a_stale_version_is_409(svc):
    q = pending(svc)
    with pytest.raises(StaleRevision, match="quote changed; reload"):
        svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1", expected_version=q.version - 1)
    with pytest.raises(StaleRevision):
        svc.approve(q.quote_id, q.revision - 1, "mgr.maria", MANAGERS, key="a2")
    assert svc.repo.get(q.quote_id).state == S.PENDING_APPROVAL


def test_every_step_is_in_quote_event_with_identity_version_and_key(svc):
    q = pending(svc)
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1")
    svc.issue(q.quote_id, q.revision, "rep.alice", key="i1", inputs_as_of=fx.fresh_inputs())
    trail = [(e.action, e.actor, e.quote_version, e.idempotency_key) for e in svc.repo.events(q.quote_id)]
    assert trail[-3:] == [("submit", "rep.alice", q.version, "submit-1"), ("approve", "mgr.maria", q.version, "a1"),
                          ("issue", "rep.alice", q.version, "i1")]


def test_a_failed_write_leaves_nothing_behind_on_lakebase():
    """The quote, lines, version, approval, event and key are one transaction: a failure in the middle rolls back."""
    db = sqlite_db()
    repo = LakebaseQuoteRepository(db)
    svc = service(repo)
    q = pending(svc)
    before = (len(repo.events(q.quote_id)), repo.get(q.quote_id).revision)
    original = repo._write

    def boom(tx, quote, event, key):
        original(tx, quote, event, key)
        raise RuntimeError("the connection dropped after the writes")
    repo._write = boom
    with pytest.raises(RuntimeError):
        svc.approve(q.quote_id, q.revision, "mgr.maria", MANAGERS, key="a1")
    repo._write = original
    assert (len(repo.events(q.quote_id)), repo.get(q.quote_id).revision) == before
    assert repo.approvals(q.quote_id) == [] and repo.idempotent_result("a1") is None
