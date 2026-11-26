"""The agent's memory (AI-20), on Lakebase: two kinds, different lifetimes, different owners.

  short-term  the conversation on a quote, per rep (conversation): resumed when the quote is reopened, kept 30 days
  long-term   facts a rep confirmed about a customer (customer_memory: "Lakeside is always latex-free"), kept until
              the rep or the account owner removes them

Rules, in the schema and the code, not in a prompt:
  - isolation: a rep loads only their own quote and conversation (owner_rep), and only their own or shared facts
  - confirm before save: a fact is written only with confirmed_by (NOT NULL in the table; refused here before that);
    the agent can only propose one (tools.propose_memory), the rep confirms in the app, QuoteService.confirm_fact
    writes it
  - memory informs, it never replaces a requirement: suggested_requirements only suggests; a remembered latex-free
    becomes a hard requirement when the rep confirms it on the quote (QuoteService.confirm(..., from_memory=...))
  - state is data, not chat: the quote's state is read from the quote table, never from the conversation

Same code on Lakebase (psycopg) and on SQLite (tests, the local demo).
"""
from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from quote_ai.storage.lakebase import Db
from quote_ai.storage.repository import QuoteNotFound

RETENTION = timedelta(days=30)
# customer facts that map onto a hard requirement (field, how to read the value)
REQUIREMENT_FACTS = {"latex_free": bool, "powder_free": bool, "chemo_tested": bool, "sterile": bool,
                     "material": str, "intended_use": str}


class MemoryRefused(PermissionError):
    """A write the memory rules don't allow (no confirmation, someone else's fact)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _value(kind, text: str):
    return text.strip().lower() in ("true", "yes", "1") if kind is bool else text


def load_context(db: Db, rep: str, quote_id: str) -> dict:
    """The quote (only if this rep owns it) and the confirmed facts about its customer, from Lakebase. Another rep's
    quote is not found, exactly as if it did not exist."""
    with db.tx() as tx:
        q = tx.one("SELECT quote_id, account_id, owner_rep, state, version, revision FROM quote "
                   "WHERE quote_id = %s AND owner_rep = %s", (quote_id, rep))
        if q is None:
            raise QuoteNotFound(quote_id)
        facts = tx.query("SELECT fact_key, fact_value, confirmed_by FROM customer_memory WHERE account_id = %s "
                         "AND (owner_rep = %s OR shared) ORDER BY fact_key", (q["account_id"], rep))
    return {"quote": q, "customer_facts": facts}


class Memory:
    def __init__(self, db: Db, clock: Callable[[], datetime] = _now, retention: timedelta = RETENTION) -> None:
        self.db, self.clock, self.retention = db, clock, retention

    def load_context(self, rep: str, quote_id: str) -> dict:
        return load_context(self.db, rep, quote_id) | {"messages": self.messages(rep, quote_id)}

    # ------------------------------------------------------------ short-term: the conversation

    def messages(self, rep: str, quote_id: str, limit: int = 40) -> list[dict]:
        with self.db.tx() as tx:
            rows = tx.query("SELECT role, content FROM conversation WHERE quote_id = %s AND owner_rep = %s "
                            "ORDER BY seq", (quote_id, rep))
        return rows[-limit:]

    def append(self, rep: str, quote_id: str, role: str, content: str) -> int:
        if role not in ("user", "assistant"):
            raise ValueError(f"role {role!r}")
        with self.db.tx() as tx:
            row = tx.one("SELECT max(seq) AS seq FROM conversation WHERE quote_id = %s AND owner_rep = %s",
                         (quote_id, rep))
            seq = (row["seq"] or 0) + 1 if row else 1
            tx.execute("INSERT INTO conversation (quote_id, owner_rep, seq, role, content, created_at) "
                       "VALUES (%s, %s, %s, %s, %s, %s)", (quote_id, rep, seq, role, content, self.clock()))
        return seq

    def purge(self) -> int:
        """Retention: conversation messages older than 30 days. Customer facts are not touched."""
        with self.db.tx() as tx:
            return tx.execute("DELETE FROM conversation WHERE created_at < %s", (self.clock() - self.retention,))

    # ------------------------------------------------------------ long-term: confirmed customer facts

    def facts(self, account: str, rep: str) -> list[dict]:
        with self.db.tx() as tx:
            return tx.query("SELECT fact_key, fact_value, owner_rep, shared, confirmed_by, confirmed_at "
                            "FROM customer_memory WHERE account_id = %s AND (owner_rep = %s OR shared) "
                            "ORDER BY fact_key", (account, rep))

    def save_fact(self, account: str, fact_key: str, fact_value: str, owner_rep: str, confirmed_by: str | None,
                  shared: bool = False) -> dict:
        """Write a fact the rep confirmed. Without confirmed_by nothing is written. A fact another rep owns is theirs
        to change."""
        if not confirmed_by:
            raise MemoryRefused("a customer fact is saved only when a rep confirms it")
        with self.db.tx() as tx:
            cur = tx.one("SELECT owner_rep FROM customer_memory WHERE account_id = %s AND fact_key = %s",
                         (account, fact_key))
            if cur and cur["owner_rep"] != owner_rep:
                raise MemoryRefused(f"{fact_key} for {account} belongs to {cur['owner_rep']}")
            tx.execute("DELETE FROM customer_memory WHERE account_id = %s AND fact_key = %s", (account, fact_key))
            tx.execute("INSERT INTO customer_memory (account_id, fact_key, fact_value, owner_rep, shared, "
                       "confirmed_by, confirmed_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                       (account, fact_key, fact_value, owner_rep, shared, confirmed_by, self.clock()))
        return {"account": account, "fact_key": fact_key, "fact_value": fact_value, "confirmed_by": confirmed_by}

    def forget(self, account: str, fact_key: str, rep: str) -> bool:
        with self.db.tx() as tx:
            return tx.execute("DELETE FROM customer_memory WHERE account_id = %s AND fact_key = %s AND owner_rep = %s",
                              (account, fact_key, rep)) == 1

    def suggested_requirements(self, account: str, rep: str, requirements=None) -> dict:
        """Facts that could become hard requirements, where the quote doesn't already say so. A suggestion only."""
        out = {}
        for f in self.facts(account, rep):
            kind = REQUIREMENT_FACTS.get(f["fact_key"])
            if kind is None:
                continue
            v = _value(kind, f["fact_value"])
            if requirements is None or getattr(requirements, f["fact_key"], None) != v:
                out[f["fact_key"]] = v
        return out


def from_env(repo=None) -> Memory:
    """On the Lakebase repository's database when quotes are on Lakebase; otherwise a SQLite file
    (QUOTE_AI_MEMORY_DB, default in memory) for the local demo."""
    from quote_ai.storage.lakebase import sqlite_db
    db = getattr(repo, "db", None)
    return Memory(db if isinstance(db, Db) else sqlite_db(os.environ.get("QUOTE_AI_MEMORY_DB", ":memory:")))
