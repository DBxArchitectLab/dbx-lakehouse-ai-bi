"""Quotes on Lakebase (AI-20): QuoteService unchanged on the Lakebase repository (SQLite running the same statements),
the migration from Delta with its proof, writes paused, and the agent on the Agent Runtime (server and client)."""
import json

import pytest

import lakeside_fixtures as fx
from agent_fixtures import confirmed, service
from quote_ai.domain.models import QuoteState as S
from quote_ai.ops import migrate_quotes_to_lakebase as mig
from quote_ai.storage.delta_repository import DeltaQuoteRepository
from quote_ai.storage.lakebase import schema_statements, sqlite_db
from quote_ai.storage.lakebase_repository import LakebaseQuoteRepository, ReadOnly, ReadOnlyRepository
from quote_ai.storage.repository import QuoteNotFound, StaleRevision
from test_delta_repository import SqliteSql
from test_quote_service import checks


@pytest.fixture
def svc():
    return service(LakebaseQuoteRepository(sqlite_db()))


def test_the_schema_is_the_one_file_on_both_dialects():
    pg, lite = schema_statements("postgres"), schema_statements("sqlite")
    assert any(s.startswith("COMMENT ON TABLE customer_memory") for s in pg)
    assert not any(s.upper().startswith("COMMENT ON") for s in lite)
    assert {s.split()[5] for s in lite if s.startswith("CREATE TABLE")} == {
        "quote", "quote_line", "quote_version", "quote_event", "idempotency_key", "approval", "customer_memory",
        "conversation"}


def test_the_lakeside_transaction_end_to_end_on_lakebase(svc):
    q = svc.create("Lakeside Regional Clinic", "rep.alice", fx.LAKESIDE_REQUEST, fx.lakeside_r1(), quote_id="Q-1")
    q = svc.confirm(q.quote_id, q.revision, "rep.alice", fx.LAKESIDE_SPLIT, "box", 100)
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))
    q = svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=8.00)
    q = svc.submit(q.quote_id, q.revision, "rep.alice", key="submit-1")
    q = svc.approve(q.quote_id, q.revision, "mgr.maria", {"DBxLab_Sales_Managers"}, key="approve-1")
    q = svc.issue(q.quote_id, q.revision, "rep.alice", key="issue-1", inputs_as_of=fx.fresh_inputs())
    repo = svc.repo
    assert repo.get("Q-1").state == S.ISSUED
    assert [e.action for e in repo.events("Q-1")] == ["create", "confirm", "shortlist", "price", "submit", "approve",
                                                      "issue"]
    with repo.db.tx() as tx:
        versions = tx.query("SELECT version, state FROM quote_version WHERE quote_id = 'Q-1' ORDER BY version")
        lines = tx.query("SELECT size, unit_price FROM quote_line WHERE quote_id = 'Q-1' ORDER BY line_no")
    assert [v["version"] for v in versions] == [1, 2, 3, 4] and versions[-1]["state"] == "ISSUED"
    assert [(l["size"], l["unit_price"]) for l in lines] == [("S", 8.0), ("M", 8.0), ("L", 8.0), ("XL", 8.0)]
    a, = repo.approvals("Q-1")
    assert (a["version"], a["approver"], a["decision"], a["idempotency_key"]) == (4, "mgr.maria", "approved",
                                                                                  "approve-1")


def test_a_stale_revision_is_quote_changed_reload(svc):
    _, q = confirmed(svc)
    svc.change_state(q.quote_id, q.revision, "rep.alice", S.CANCELLED)          # the first tab
    with pytest.raises(StaleRevision, match="changed"):
        svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))      # the second tab, same revision
    with pytest.raises(StaleRevision, match="quote changed; reload"):
        svc.repo.update(q, q.revision, svc.repo.events(q.quote_id)[-1])


def test_my_quotes_and_not_found(svc):
    confirmed(svc)
    svc.create("Harbor Point", "rep.bob", "50 boxes", fx.lakeside_r1(), quote_id="Q-B")
    assert [q.quote_id for q in svc.mine("rep.bob")] == ["Q-B"]
    with pytest.raises(QuoteNotFound):
        svc.repo.get("nope")


def test_writes_paused_reads_work():
    repo = LakebaseQuoteRepository(sqlite_db())
    svc, q = confirmed(service(repo))
    paused = service(ReadOnlyRepository(repo))
    assert paused.repo.get(q.quote_id).quote_id == q.quote_id
    with pytest.raises(ReadOnly):
        paused.change_state(q.quote_id, q.revision, "rep.alice", S.CANCELLED)


# ------------------------------------------------------------------ the migration from Delta

from quote_ai.fixtures.agents import delta_with_quotes  # noqa: E402  (in the package since AI-22)


def test_the_migration_copies_and_proves_counts_and_checksums_per_table():
    sql = delta_with_quotes()
    db = sqlite_db()
    steps = []
    report = mig.migrate(lambda t: sql.query(f"SELECT * FROM {t}"), db, pause=lambda: steps.append("pause"),
                         switch=lambda: steps.append("switch"), sync=lambda: steps.append("sync"),
                         resume=lambda: steps.append("resume"))
    assert steps == ["pause", "switch", "sync", "resume"]
    assert all(report[t]["match"] for t in mig.TABLES) and report["quote_version"]["match"]
    assert report["quote"]["delta"] == report["quote"]["lakebase"] and report["quote"]["delta"]["rows"] == 2
    assert report["quote_event"]["lakebase"]["rows"] == 5          # Lakeside: 4 moves; Harbor Point: 1
    lake = service(LakebaseQuoteRepository(db))
    q = lake.repo.get("Q-LAKESIDE")
    assert q.state == S.PRICED and [e.action for e in lake.repo.events(q.quote_id)][-1] == "price"
    q = lake.submit(q.quote_id, q.revision, "rep.alice", key="after-switch")          # QuoteService on Lakebase
    assert q.state == S.PENDING_APPROVAL


def test_a_mismatch_stops_the_migration_and_copies_nothing():
    """A row lost on the way (here: the copy drops one line) -> counts differ -> stop and roll back."""
    sql = delta_with_quotes()
    db = sqlite_db()
    original = mig._copy

    def lossy(tx, table, rows):
        original(tx, table, rows[:-1] if table == "quote_line" else rows)
    mig._copy = lossy
    try:
        with pytest.raises(mig.MigrationMismatch, match="quote_line"):
            mig.copy_and_prove(lambda t: sql.query(f"SELECT * FROM {t}"), db)
    finally:
        mig._copy = original
    with db.tx() as tx:
        assert tx.one("SELECT count(*) AS n FROM quote")["n"] == 0          # rolled back: one truth at a time


def test_the_checksum_ignores_order_but_not_values():
    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    assert mig.count_and_checksum(rows, ["a", "b"]) == mig.count_and_checksum(rows[::-1], ["a", "b"])
    assert mig.count_and_checksum(rows, ["a", "b"]) != mig.count_and_checksum([rows[0], {"a": 2, "b": "z"}],
                                                                              ["a", "b"])
    assert mig.canonical(8) == mig.canonical(8.0) == "8.00"


def test_the_sync_sql_keeps_the_names_analytics_reads():
    from quote_ai.jobs import lakebase_job
    from quote_ai.storage import sql_files
    stmts = lakebase_job.sync_statements(sql_files.names_for("cat", {}), "quote_ai_lakebase_dev")
    targets = [s.split("TABLE ")[1].split()[0] for s in stmts]
    assert targets == ["cat.quote_ai_sales.quote", "cat.quote_ai_sales.quote_line", "cat.quote_ai_sales.quote_event",
                       "cat.quote_ai_sales.quote_approval"]
    assert all("FROM quote_ai_lakebase_dev.public." in s for s in stmts)
    with pytest.raises(SystemExit):
        lakebase_job.sync_statements(sql_files.names_for("cat", {}), "x; DROP TABLE y")


# ------------------------------------------------------------------ the agent on the Agent Runtime

def test_the_agent_app_streams_events_and_the_app_reads_them():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from agent_fixtures import agent
    from quote_ai.agents.server import RemoteAgent, agent_app_name, create_app, parse_sse
    _, q = confirmed()
    client = TestClient(create_app(agent()))
    assert client.get("/api/health").json() == {"status": "ok"}

    def post(url, body, headers):
        r = client.post(url.replace("http://agent", ""), json=body, headers=headers)
        return iter(r.text.splitlines(keepends=True))
    remote = RemoteAgent("http://agent", headers={"Authorization": "Bearer t"}, post=post)
    events = list(remote.events([{"role": "user", "content": "Is Glove B chemo-tested?"}], q, "rep.alice",
                                [{"fact_key": "latex_free", "fact_value": "true", "confirmed_by": "rep.alice"}]))
    assert [e["type"] for e in events][0] == "step" and events[-1]["type"] == "done"
    assert "not verified" in events[-1]["answer"]
    assert agent_app_name("quote-ai-dev") == "quote-ai-agent-dev"
    with pytest.raises(ValueError):
        agent_app_name("quote-ai-agent-dev")
    assert list(parse_sse(["event: x\n", 'data: {"a": 1}\n', "\n"])) == [{"a": 1}]
    json.dumps(events, default=str)
