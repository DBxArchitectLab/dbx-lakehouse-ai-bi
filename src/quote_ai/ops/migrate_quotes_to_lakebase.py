"""Moving the source of truth for quotes from Delta to Lakebase (AI-20, plan 4.2), in five steps:

  1. pause writes       the app's quote screens go read-only (QUOTE_AI_READ_ONLY=1: ReadOnlyRepository, HTTP 503)
  2. copy               quote_ai_sales.quote, quote_line, quote_event (Delta) into Lakebase, in ONE transaction; each
                        quote's current version into quote_version
  3. prove it           row counts and an order-independent checksum per table, Delta against what Lakebase holds,
                        read back inside the same transaction; any difference raises MigrationMismatch and the
                        transaction rolls back: nothing is half-copied
  4. switch             QuoteService on its Lakebase adapter (QUOTE_AI_STORE=lakebase): same states, same rules
  5. sync for analytics the Delta tables become a read-only copy under the same names (the old ones kept as
                        *_pre_ai20 for a release), so the metric views, Genie and the win model don't change

One source of truth at a time, and a proof at the handover. migrate() takes the steps as functions, so the job
(quote_ai.jobs.lakebase_job) and the tests (SQLite on both sides) run the same code.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from datetime import datetime

from quote_ai.domain.models import Quote
from quote_ai.rules.approval import terms_hash
from quote_ai.storage.lakebase import Db, Tx

TABLES = ("quote", "quote_line", "quote_event")
# The columns both sides hold with the same meaning: what the checksum covers
COLUMNS = {
    "quote": ["quote_id", "state", "version", "revision", "doc"],
    "quote_line": ["quote_id", "line_no", "size", "quantity", "unit", "sku_id", "unit_price", "list_price",
                   "price_book_id"],
    "quote_event": ["quote_id", "revision", "quote_version", "action", "from_state", "to_state", "actor",
                    "idempotency_key"],
}


class MigrationMismatch(RuntimeError):
    pass


def canonical(v) -> str:
    """One text form per value on both sides: numbers to two decimals, timestamps in ISO, null as empty."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, (int, float)) or type(v).__name__ == "Decimal":
        return f"{float(v):.2f}"
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def count_and_checksum(rows: Iterable[dict], columns: list[str]) -> tuple[int, str]:
    """(rows, checksum): the sum of a hash per row, so the order of rows doesn't matter but every value does."""
    n, total = 0, 0
    for r in rows:
        n += 1
        h = hashlib.sha256("\x1f".join(canonical(r.get(c)) for c in columns).encode()).hexdigest()
        total = (total + int(h[:16], 16)) % (1 << 64)
    return n, f"{total:016x}"


def _copy(tx: Tx, table: str, rows: list[dict]) -> None:
    for r in rows:
        if table == "quote":
            q = Quote.model_validate_json(r["doc"])
            tx.execute("INSERT INTO quote (quote_id, account_id, owner_rep, state, version, revision, total, doc, "
                       "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                       (r["quote_id"], q.account, q.rep, r["state"], r["version"], r["revision"], r.get("total"),
                        r["doc"], r.get("created_at") or r["updated_at"], r["updated_at"]))
            tx.execute("INSERT INTO quote_version (quote_id, version, state, terms_hash, doc, created_at) "
                       "VALUES (%s, %s, %s, %s, %s, %s)", (q.quote_id, q.version, q.state.value, terms_hash(q),
                                                            r["doc"], r["updated_at"]))
        elif table == "quote_line":
            tx.execute("INSERT INTO quote_line (quote_id, version, line_no, size, quantity, unit, sku_id, unit_price, "
                       "list_price, price_book_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                       tuple(r.get(c) for c in ("quote_id", "version", "line_no", "size", "quantity", "unit",
                                                "sku_id", "unit_price", "list_price", "price_book_id")))
        else:
            tx.execute("INSERT INTO quote_event (quote_id, quote_version, revision, action, from_state, to_state, "
                       "actor, at, trace_id, idempotency_key, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
                       "%s, %s)", tuple(r.get(c) for c in ("quote_id", "quote_version", "revision", "action",
                                                           "from_state", "to_state", "actor", "at", "trace_id",
                                                           "idempotency_key", "detail")))
            if r.get("idempotency_key"):
                tx.execute("INSERT INTO idempotency_key (idempotency_key, quote_id, action, revision, created_at) "
                           "VALUES (%s, %s, %s, %s, %s)", (r["idempotency_key"], r["quote_id"], r["action"],
                                                           r["revision"], r["at"]))


def copy_and_prove(read_delta: Callable[[str], list[dict]], db: Db) -> dict:
    """Steps 2 and 3 in one Lakebase transaction: copy every table, read it back, compare. A mismatch rolls back."""
    report = {}
    with db.tx() as tx:
        if tx.one("SELECT count(*) AS n FROM quote")["n"]:
            raise MigrationMismatch("Lakebase already holds quotes: the migration runs once, into empty tables")
        source = {t: read_delta(t) for t in TABLES}
        for t in TABLES:
            _copy(tx, t, source[t])
        for t in TABLES:
            d = count_and_checksum(source[t], COLUMNS[t])
            lb = count_and_checksum(tx.query(f"SELECT {', '.join(COLUMNS[t])} FROM {t}"), COLUMNS[t])
            report[t] = {"delta": {"rows": d[0], "checksum": d[1]}, "lakebase": {"rows": lb[0], "checksum": lb[1]},
                         "match": d == lb}
            if d != lb:
                raise MigrationMismatch(f"{t}: Delta {d} != Lakebase {lb}; nothing was copied")
        versions = tx.one("SELECT count(*) AS n FROM quote_version")["n"]
        report["quote_version"] = {"rows": versions, "match": versions == len(source["quote"])}
    return report


def migrate(read_delta: Callable[[str], list[dict]], db: Db, pause: Callable[[], None],
            switch: Callable[[], None], sync: Callable[[], None], resume: Callable[[], None]) -> dict:
    """The five steps. Writes stay paused if the proof fails (resume only after a successful switch), so the old
    store keeps being the truth and nothing was copied."""
    pause()
    report = copy_and_prove(read_delta, db)
    switch()
    sync()
    resume()
    return report


def as_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True)
