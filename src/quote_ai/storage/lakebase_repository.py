"""QuoteRepository on Lakebase (AI-20): the same interface as the in-memory and Delta repositories, so QuoteService
doesn't change; only where the quote is stored does. The states and rules of AI-07 are untouched.

One transaction per move (what Delta could not give, AI-07): lock the quote row (SELECT ... FOR UPDATE), check the
revision the caller read (optimistic locking: a stale one is StaleRevision, HTTP 409 "quote changed; reload"), record
the idempotency key (a key seen before is refused, so a double click acts once), then the quote row, its lines, its
version, its approval (AI-21) and its quote_event. All of it commits together, or none of it does.

Implemented against a DB-API connection (quote_ai.storage.lakebase.Db): psycopg on Lakebase, sqlite3 in the tests
(tests/quote_ai/test_lakebase_repository.py runs QuoteService on it, like test_delta_repository.py).
"""
from __future__ import annotations

import json
from datetime import datetime

from quote_ai.domain.models import Approval, Quote, QuoteEvent
from quote_ai.rules.approval import terms_hash
from quote_ai.storage.lakebase import Db, Tx
from quote_ai.storage.repository import QuoteNotFound, StaleRevision


class ReadOnly(RuntimeError):
    """Writes are paused (the AI-20 migration): HTTP 503, try again in a few minutes."""


class LakebaseQuoteRepository:
    def __init__(self, db: Db) -> None:
        self.db = db

    # ------------------------------------------------------------ reads

    def get(self, quote_id: str) -> Quote:
        with self.db.tx() as tx:
            row = tx.one("SELECT doc FROM quote WHERE quote_id = %s", (quote_id,))
        if row is None:
            raise QuoteNotFound(quote_id)
        return Quote.model_validate_json(row["doc"])

    def events(self, quote_id: str) -> list[QuoteEvent]:
        with self.db.tx() as tx:
            rows = tx.query("SELECT quote_id, quote_version, revision, action, from_state, to_state, actor, at, "
                            "trace_id, idempotency_key, detail FROM quote_event WHERE quote_id = %s ORDER BY revision",
                            (quote_id,))
        return [QuoteEvent.model_validate(r | {"detail": json.loads(r["detail"] or "{}")}) for r in rows]

    def list_quotes(self, rep: str | None = None) -> list[Quote]:
        where, params = ("WHERE owner_rep = %s", (rep,)) if rep else ("", ())
        with self.db.tx() as tx:
            rows = tx.query(f"SELECT doc FROM quote {where} ORDER BY updated_at DESC, quote_id", params)
        return [Quote.model_validate_json(r["doc"]) for r in rows]

    def pending_approval(self) -> list[Quote]:
        """AI-21: the managers' inbox, every rep's quotes waiting for a manager."""
        with self.db.tx() as tx:
            rows = tx.query("SELECT doc FROM quote WHERE state = %s ORDER BY updated_at", ("PENDING_APPROVAL",))
        return [Quote.model_validate_json(r["doc"]) for r in rows]

    def idempotent_result(self, key: str) -> str | None:
        with self.db.tx() as tx:
            row = tx.one("SELECT quote_id FROM idempotency_key WHERE idempotency_key = %s", (key,))
        return row["quote_id"] if row else None

    def remember(self, key: str, quote_id: str) -> None:
        """Keys are written with the move that used them, in its transaction: nothing to do."""

    def approvals(self, quote_id: str) -> list[dict]:
        with self.db.tx() as tx:
            return tx.query("SELECT quote_id, version, terms_hash, approver, decision, decided_at, idempotency_key "
                            "FROM approval WHERE quote_id = %s ORDER BY decided_at", (quote_id,))

    def latest_approval(self, quote_id: str) -> Approval | None:
        """AI-21: the latest approval recorded for the quote (any version): can_issue compares it with the quote."""
        rows = [r for r in self.approvals(quote_id) if r["decision"] == "approved"]
        if not rows:
            return None
        r = rows[-1]
        at = r["decided_at"] if isinstance(r["decided_at"], datetime) else datetime.fromisoformat(r["decided_at"])
        return Approval(approver=r["approver"], terms_hash=r["terms_hash"], quote_version=r["version"], at=at)

    # ------------------------------------------------------------ writes: one transaction each

    def _write(self, tx: Tx, q: Quote, event: QuoteEvent, key: str | None) -> None:
        tx.execute("DELETE FROM quote_line WHERE quote_id = %s", (q.quote_id,))
        for l in q.lines:
            tx.execute("INSERT INTO quote_line (quote_id, version, line_no, size, quantity, unit, sku_id, unit_price, "
                       "list_price, price_book_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                       (q.quote_id, q.version, l.line_no, l.size, l.quantity, l.unit, l.sku_id, l.unit_price,
                        l.list_price, l.price_book_id))
        tx.execute("INSERT INTO quote_version (quote_id, version, state, terms_hash, doc, created_at) "
                   "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (quote_id, version) DO UPDATE SET "
                   "state = excluded.state, terms_hash = excluded.terms_hash, doc = excluded.doc",
                   (q.quote_id, q.version, q.state.value, terms_hash(q), q.model_dump_json(), event.at))
        if event.action in ("approve", "submit") and q.approval is not None \
                and q.approval.quote_version == q.version:
            try:
                tx.execute("INSERT INTO approval (quote_id, version, terms_hash, approver, decision, decided_at, "
                           "idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                           (q.quote_id, q.version, q.approval.terms_hash, q.approval.approver, "approved",
                            q.approval.at, key))
            except self.db.integrity as e:
                raise StaleRevision(f"{q.quote_id} version {q.version} is already approved") from e
        if event.action == "reject":
            tx.execute("INSERT INTO approval (quote_id, version, terms_hash, approver, decision, decided_at, "
                       "idempotency_key) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (q.quote_id, q.version, terms_hash(q), event.actor, "rejected", event.at, key))
        tx.execute("INSERT INTO quote_event (quote_id, quote_version, revision, action, from_state, to_state, actor, "
                   "at, trace_id, idempotency_key, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                   (event.quote_id, event.quote_version, event.revision, event.action,
                    event.from_state.value if event.from_state else None, event.to_state.value, event.actor,
                    event.at, event.trace_id, event.idempotency_key, json.dumps(event.detail, default=str)))

    def insert(self, quote: Quote, event: QuoteEvent) -> None:
        with self.db.tx() as tx:
            if tx.one("SELECT 1 AS x FROM quote WHERE quote_id = %s", (quote.quote_id,)):
                raise StaleRevision(f"{quote.quote_id} exists")
            tx.execute("INSERT INTO quote (quote_id, account_id, owner_rep, state, version, revision, total, doc, "
                       "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                       (quote.quote_id, quote.account, quote.rep, quote.state.value, quote.version, quote.revision,
                        quote.total, quote.model_dump_json(), event.at, event.at))
            self._write(tx, quote, event, None)

    def update(self, quote: Quote, expected_revision: int, event: QuoteEvent, key: str | None = None) -> None:
        with self.db.tx() as tx:
            row = tx.one("SELECT revision FROM quote WHERE quote_id = %s FOR UPDATE", (quote.quote_id,))
            if row is None:
                raise QuoteNotFound(quote.quote_id)
            if row["revision"] != expected_revision:
                raise StaleRevision(f"quote changed; reload ({quote.quote_id}: revision {row['revision']}, "
                                    f"expected {expected_revision})")
            if key is not None:
                try:
                    tx.execute("INSERT INTO idempotency_key (idempotency_key, quote_id, action, revision, created_at) "
                               "VALUES (%s, %s, %s, %s, %s)", (key, quote.quote_id, event.action, quote.revision,
                                                               event.at))
                except self.db.integrity as e:
                    raise StaleRevision(f"idempotency key {key} already used") from e
            n = tx.execute("UPDATE quote SET state = %s, version = %s, revision = %s, total = %s, doc = %s, "
                           "updated_at = %s WHERE quote_id = %s AND revision = %s",
                           (quote.state.value, quote.version, quote.revision, quote.total, quote.model_dump_json(),
                            event.at, quote.quote_id, expected_revision))
            if n != 1:
                raise StaleRevision(f"quote changed; reload ({quote.quote_id})")
            self._write(tx, quote, event, key)


class ReadOnlyRepository:
    """Any repository with its writes paused (the AI-20 migration's first step): reads work, writes are refused."""

    def __init__(self, repo) -> None:
        self.repo = repo

    def __getattr__(self, name):
        if name in ("insert", "update", "remember"):
            def refuse(*_a, **_k):
                raise ReadOnly("quotes are read-only for a few minutes (moving to Lakebase); try again shortly")
            return refuse
        return getattr(self.repo, name)
