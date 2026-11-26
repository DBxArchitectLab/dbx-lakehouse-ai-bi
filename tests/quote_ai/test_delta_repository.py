"""The Delta quote repository (AI-07), on SQLite running the same statements: QuoteService unchanged on top of it."""
import sqlite3
import threading

import pytest

import lakeside_fixtures as fx
from quote_ai.domain.models import QuoteState as S
from quote_ai.services import QuoteService
from quote_ai.storage.delta_repository import DeltaQuoteRepository
from quote_ai.storage.product_catalog import DemoProductCatalog
from quote_ai.storage.repository import QuoteNotFound, StaleRevision

from quote_ai.fixtures.agents import DDL, SqliteSql  # noqa: E402,F401  (in the package since AI-22)


@pytest.fixture
def sql():
    return SqliteSql()


@pytest.fixture
def svc(sql):
    return QuoteService(DeltaQuoteRepository(sql, "main"), clock=lambda: fx.NOW, trace_id=lambda: "tr-1")


def test_the_lakeside_transaction_end_to_end_on_delta(svc, sql):
    from test_quote_service import checks
    q = svc.create("Lakeside Regional Clinic", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-1")
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))
    q = svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=8.00)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="submit-1")
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", {"sales_manager"}, key="approve-1")
    q = svc.issue(q.quote_id, q.revision, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    assert svc.repo.get("Q-1").state == S.ISSUED
    assert [e.action for e in svc.repo.events("Q-1")] == [
        "create", "confirm", "shortlist", "price", "submit", "approve", "issue"]
    lines = sql.query("SELECT size, quantity, sku_id, unit_price FROM quote_line WHERE quote_id = 'Q-1' "
                      "ORDER BY line_no")
    assert [(l["size"], l["quantity"], l["sku_id"], l["unit_price"]) for l in lines] == [
        ("S", 60, "GLOVE-A-S", 8.0), ("M", 140, "GLOVE-A-M", 8.0), ("L", 140, "GLOVE-A-L", 8.0),
        ("XL", 60, "GLOVE-A-XL", 8.0)]
    assert sql.query("SELECT state, total FROM quote")[0] == {"state": "ISSUED", "total": 3200.0}


def test_v1_quick_quote_writes_four_rows_one_per_move(svc, sql):
    from test_quote_service import read_lakeside
    cat = DemoProductCatalog()
    q = read_lakeside(svc)
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    svc.quick_quote(q.quote_id, q.revision, "rep.alice", cat.offerable_skus(), cat.price_book())
    events = sql.query("SELECT to_state, actor, trace_id FROM quote_event ORDER BY revision")
    assert [e["to_state"] for e in events] == ["DRAFT", "REQUIREMENTS_CONFIRMED", "SHORTLISTED", "PRICED"]
    assert {e["actor"] for e in events} == {"rep.alice"} and {e["trace_id"] for e in events} == {"tr-1"}
    assert sql.query("SELECT price_method, total FROM quote")[0] == {"price_method": "v1_list_price",
                                                                     "total": 3440.0}


def test_a_refused_move_writes_no_row(svc, sql):
    from test_quote_service import read_lakeside
    from quote_ai.rules.quote_states import InvalidTransition
    q = read_lakeside(svc)
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    n = len(sql.statements)
    with pytest.raises(InvalidTransition):
        svc.change_state(q.quote_id, q.revision, "rep.alice", S.ISSUED)
    assert len(sql.statements) == n                       # not a single write was sent
    assert len(sql.query("SELECT * FROM quote_event")) == 2


def test_a_stale_revision_is_refused_and_the_winner_is_kept(svc, sql):
    q = svc.create("Lakeside", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-2")
    svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    with pytest.raises(StaleRevision):
        svc.confirm(q.quote_id, q.revision, "rep.bob", fx.LAKESIDE_SPLIT, "box", 100)   # read before the first
    assert [e["actor"] for e in sql.query("SELECT actor FROM quote_event ORDER BY revision")] == ["rep.alice"] * 2


def test_the_conditional_update_is_the_lock(sql):
    """Two writers that both read revision 2: the repository's UPDATE ... AND revision = 2 lets only one through."""
    repo = DeltaQuoteRepository(sql, "main")
    svc = QuoteService(repo, clock=lambda: fx.NOW)
    q = svc.create("Lakeside", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-3")
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    from quote_ai.domain.models import QuoteEvent
    ev = QuoteEvent(quote_id="Q-3", quote_version=q.version, revision=3, action="x", from_state=q.state,
                    to_state=S.CANCELLED, actor="a", at=fx.NOW)
    repo.update(q.model_copy(update={"revision": 3, "state": S.CANCELLED}), 2, ev)
    with pytest.raises(StaleRevision):
        repo.update(q.model_copy(update={"revision": 3, "state": S.CANCELLED}), 2, ev)


def test_idempotency_keys_live_with_their_event(svc):
    from test_quote_service import to_priced
    q = to_priced(svc, asked=8.40)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="s-1")
    again = svc.submit(q.quote_id, q.revision - 1, "rep.alice", key="s-1")      # a retried call
    assert again.revision == q.revision and svc.repo.idempotent_result("s-1") == q.quote_id
    assert [e.action for e in svc.repo.events(q.quote_id)].count("submit") == 1


def test_not_found_and_my_quotes(svc):
    with pytest.raises(QuoteNotFound):
        svc.repo.get("nope")
    svc.create("A", "rep.alice", "x", fx.lakeside_r1(), quote_id="Q-A")
    svc.create("B", "rep.bob", "y", fx.lakeside_r1(), quote_id="Q-B")
    assert [q.quote_id for q in svc.mine("rep.bob")] == ["Q-B"]


def test_the_schema_name_is_checked():
    with pytest.raises(ValueError):
        DeltaQuoteRepository(SqliteSql(), "cat.schema; DROP TABLE quote")
