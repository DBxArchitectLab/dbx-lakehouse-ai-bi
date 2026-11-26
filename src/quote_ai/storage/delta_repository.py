"""QuoteRepository on Delta (AI-07): quote, quote_line and quote_event in the sales schema, through a SQL warehouse.

The same interface as the in-memory repository, so QuoteService doesn't know which one it has. The app passes a
DatabricksSql executor (the app's service principal, the warehouse from the app's resources); the tests pass a
SQLite executor running the same statements.

Optimistic locking: an update is `UPDATE quote ... WHERE quote_id = :id AND revision = :expected`; no row changed
means someone else moved the quote first (StaleRevision, HTTP 409). Delta has no multi-table transaction: the quote
row is written first (that is the lock), then its lines and its event. A failure between them leaves a quote ahead
of its event history; AI-20 moves the same tables to Lakebase, where the three writes are one transaction.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Any, Protocol

from quote_ai.domain.models import Quote, QuoteEvent
from quote_ai.storage.repository import QuoteNotFound, StaleRevision

_SCHEMA = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


class SqlExecutor(Protocol):
    def execute(self, sql: str, params: dict[str, Any] | None = None) -> int: ...      # rows affected
    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]: ...


class DatabricksSql:
    """A SQL warehouse through the Databricks SQL connector, with the app's own credentials (databricks-sdk Config:
    the app's service principal on Databricks Apps). Verify before recording: databricks-sql-connector's native
    :name parameters and the num_affected_rows column returned by UPDATE."""

    def __init__(self, warehouse_id: str, token: str | None = None) -> None:
        """token (AI-24): the signed-in rep's forwarded token, so the query runs as the rep and Unity Catalog's row
        filters and masks apply; without it, the app's own credentials. Verify before recording: access_token= in
        databricks-sql-connector with a Databricks Apps user token."""
        from databricks import sql
        from databricks.sdk.core import Config
        cfg = Config()
        path = f"/sql/1.0/warehouses/{warehouse_id}"
        if token:
            self._connect = lambda: sql.connect(server_hostname=cfg.host, http_path=path, access_token=token)
        else:
            self._connect = lambda: sql.connect(server_hostname=cfg.host, http_path=path,
                                                credentials_provider=lambda: cfg.authenticate)

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params or {})
            names = [d[0] for d in (cur.description or [])]
            if "num_affected_rows" in names:
                row = cur.fetchone()
                return int(row[names.index("num_affected_rows")] or 0)
            return max(cur.rowcount, 0)

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params or {})
            names = [d[0] for d in cur.description]
            return [dict(zip(names, r)) for r in cur.fetchall()]


_PARAM = re.compile(r"(?<!:):([A-Za-z_]\w*)")


class _Bound:
    """Passes each statement only the parameters it names (a warehouse may refuse unused ones)."""

    def __init__(self, sql: SqlExecutor) -> None:
        self._sql = sql

    @staticmethod
    def _only(sql: str, params: dict | None) -> dict:
        used = set(_PARAM.findall(sql))
        return {k: v for k, v in (params or {}).items() if k in used}

    def execute(self, sql: str, params: dict | None = None) -> int:
        return self._sql.execute(sql, self._only(sql, params))

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        return self._sql.query(sql, self._only(sql, params))


class DeltaQuoteRepository:
    def __init__(self, sql: SqlExecutor, schema: str) -> None:
        """schema: catalog.schema of the sales tables (from the bundle; never a literal)."""
        if not _SCHEMA.match(schema):
            raise ValueError(f"not a plain catalog.schema: {schema!r}")
        self.sql = _Bound(sql)
        self.t = {k: f"{schema}.{k}" for k in ("quote", "quote_line", "quote_event")}
        self._lock = threading.Lock()   # one writer per process; Delta's own conflict check covers other processes

    # ------------------------------------------------------------ reads

    def get(self, quote_id: str) -> Quote:
        rows = self.sql.query(f"SELECT doc FROM {self.t['quote']} WHERE quote_id = :id", {"id": quote_id})
        if not rows:
            raise QuoteNotFound(quote_id)
        return Quote.model_validate_json(rows[0]["doc"])

    def events(self, quote_id: str) -> list[QuoteEvent]:
        rows = self.sql.query(
            f"SELECT quote_id, quote_version, revision, action, from_state, to_state, actor, at, trace_id, "
            f"idempotency_key, detail FROM {self.t['quote_event']} WHERE quote_id = :id ORDER BY revision",
            {"id": quote_id})
        return [QuoteEvent.model_validate(r | {"detail": json.loads(r["detail"] or "{}")}) for r in rows]

    def list_quotes(self, rep: str | None = None) -> list[Quote]:
        where, params = ("WHERE rep = :rep", {"rep": rep}) if rep else ("", {})
        rows = self.sql.query(f"SELECT doc FROM {self.t['quote']} {where} ORDER BY updated_at DESC", params)
        return [Quote.model_validate_json(r["doc"]) for r in rows]

    def idempotent_result(self, key: str) -> str | None:
        rows = self.sql.query(f"SELECT quote_id FROM {self.t['quote_event']} WHERE idempotency_key = :k", {"k": key})
        return rows[0]["quote_id"] if rows else None

    def remember(self, key: str, quote_id: str) -> None:
        """Keys are stored with the event that used them (quote_event.idempotency_key): nothing to do."""

    # ------------------------------------------------------------ writes

    def _row(self, q: Quote, at) -> dict:
        return {"id": q.quote_id, "version": q.version, "revision": q.revision, "account": q.account, "rep": q.rep,
                "state": q.state.value, "request_text": q.request_text,
                "requirements": q.requirements.model_dump_json(), "product_id": q.product_id, "total": q.total,
                "price_method": q.price_method, "doc": q.model_dump_json(), "at": at}

    def _lines(self, q: Quote) -> None:
        self.sql.execute(f"DELETE FROM {self.t['quote_line']} WHERE quote_id = :id", {"id": q.quote_id})
        for l in q.lines:
            self.sql.execute(
                f"INSERT INTO {self.t['quote_line']} (quote_id, version, line_no, size, quantity, unit, sku_id, "
                f"unit_price, list_price, price_book_id) VALUES (:id, :version, :line_no, :size, :quantity, :unit, "
                f":sku_id, :unit_price, :list_price, :price_book_id)",
                {"id": q.quote_id, "version": q.version, "line_no": l.line_no, "size": l.size,
                 "quantity": l.quantity, "unit": l.unit, "sku_id": l.sku_id, "unit_price": l.unit_price,
                 "list_price": l.list_price, "price_book_id": l.price_book_id})

    def _event(self, e: QuoteEvent) -> None:
        self.sql.execute(
            f"INSERT INTO {self.t['quote_event']} (quote_id, quote_version, revision, action, from_state, to_state, "
            f"actor, at, trace_id, idempotency_key, detail) VALUES (:id, :qv, :rev, :action, :from_state, "
            f":to_state, :actor, :at, :trace_id, :key, :detail)",
            {"id": e.quote_id, "qv": e.quote_version, "rev": e.revision, "action": e.action,
             "from_state": e.from_state.value if e.from_state else None, "to_state": e.to_state.value,
             "actor": e.actor, "at": e.at, "trace_id": e.trace_id, "key": e.idempotency_key,
             "detail": json.dumps(e.detail, default=str)})

    def insert(self, quote: Quote, event: QuoteEvent) -> None:
        with self._lock:
            if self.sql.query(f"SELECT 1 AS x FROM {self.t['quote']} WHERE quote_id = :id", {"id": quote.quote_id}):
                raise StaleRevision(f"{quote.quote_id} exists")
            self.sql.execute(
                f"INSERT INTO {self.t['quote']} (quote_id, version, revision, account, rep, state, request_text, "
                f"requirements, product_id, total, price_method, doc, created_at, updated_at) VALUES (:id, :version, "
                f":revision, :account, :rep, :state, :request_text, :requirements, :product_id, :total, "
                f":price_method, :doc, :at, :at)", self._row(quote, event.at))
            self._lines(quote)
            self._event(event)

    def update(self, quote: Quote, expected_revision: int, event: QuoteEvent, key: str | None = None) -> None:
        with self._lock:
            if key is not None and self.idempotent_result(key) is not None:
                raise StaleRevision(f"idempotency key {key} already used")
            changed = self.sql.execute(
                f"UPDATE {self.t['quote']} SET version = :version, revision = :revision, state = :state, "
                f"requirements = :requirements, product_id = :product_id, total = :total, "
                f"price_method = :price_method, doc = :doc, updated_at = :at "
                f"WHERE quote_id = :id AND revision = :expected",
                self._row(quote, event.at) | {"expected": expected_revision})
            if changed == 0:
                if not self.sql.query(f"SELECT 1 AS x FROM {self.t['quote']} WHERE quote_id = :id",
                                      {"id": quote.quote_id}):
                    raise QuoteNotFound(quote.quote_id)
                raise StaleRevision(f"{quote.quote_id}: changed since revision {expected_revision}")
            self._lines(quote)
            self._event(event)
