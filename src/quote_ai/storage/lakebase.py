"""A DB-API connection to Lakebase (AI-20), and the same code on SQLite for the unit tests.

  Db.tx()        one transaction: every statement in the block commits together, or none does (an exception rolls
                 back). SQL uses %s placeholders (psycopg); on SQLite they become ?, and FOR UPDATE (the row lock) is
                 dropped: SQLite locks the whole database for a write transaction, which is stronger.
  lakebase_db()  psycopg to the Lakebase instance with a short-lived OAuth token as the password (the app's service
                 principal on Databricks Apps: PGHOST, PGDATABASE, PGUSER come from the app's database resource)
  sqlite_db()    an in-memory (or file) database with lakebase_schema.sql applied: tests, the local demo
  apply_schema() src/quote_ai/rules/sql/lakebase_schema.sql, statement by statement

Verify before recording: WorkspaceClient().database.generate_database_credential(instance_names=[...]) and the
environment variables Databricks Apps sets for a database resource; sslmode=require.
"""
from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "rules" / "sql" / "lakebase_schema.sql"


class Tx:
    def __init__(self, cur, dialect: str) -> None:
        self.cur, self.dialect = cur, dialect

    def _sql(self, sql: str) -> str:
        if self.dialect == "sqlite":
            return sql.replace(" FOR UPDATE", "").replace("%s", "?")
        return sql

    def _params(self, params) -> tuple:
        if self.dialect == "sqlite":
            return tuple(p.isoformat() if isinstance(p, datetime) else p for p in params)
        return tuple(params)

    def execute(self, sql: str, params=()) -> int:
        self.cur.execute(self._sql(sql), self._params(params))
        return self.cur.rowcount

    def query(self, sql: str, params=()) -> list[dict]:
        self.cur.execute(self._sql(sql), self._params(params))
        names = [d[0] for d in self.cur.description or []]
        return [dict(zip(names, r)) for r in self.cur.fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None


class Db:
    """connect() -> a DB-API connection. integrity: the driver's unique-violation exception classes."""

    def __init__(self, connect: Callable[[], Any], dialect: str = "postgres",
                 integrity: tuple[type[BaseException], ...] = ()) -> None:
        self.connect, self.dialect, self.integrity = connect, dialect, integrity
        self._shared = None
        self._lock = threading.RLock()

    def _conn(self):
        if self.dialect == "sqlite":                 # one connection, one writer at a time
            if self._shared is None:
                self._shared = self.connect()
            return self._shared
        return self.connect()

    @contextlib.contextmanager
    def tx(self) -> Iterator[Tx]:
        with self._lock if self.dialect == "sqlite" else contextlib.nullcontext():
            conn = self._conn()
            cur = conn.cursor()
            try:
                if self.dialect == "sqlite":
                    cur.execute("BEGIN IMMEDIATE")
                yield Tx(cur, self.dialect)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            finally:
                cur.close()
                if self.dialect != "sqlite":
                    conn.close()


def schema_statements(dialect: str, path: Path = SCHEMA_FILE) -> list[str]:
    from quote_ai.storage.sql_files import statements
    out = []
    for s in statements(path.read_text(encoding="utf-8")):
        body = "\n".join(l for l in s.splitlines() if not l.strip().startswith("--")).strip()
        if body and not (dialect == "sqlite" and body.upper().startswith("COMMENT ON")):
            out.append(body)
    return out


def apply_schema(db: Db) -> int:
    stmts = schema_statements(db.dialect)
    with db.tx() as tx:
        for s in stmts:
            tx.execute(s)
    return len(stmts)


def sqlite_db(path: str = ":memory:") -> Db:
    def connect():
        c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        c.execute("PRAGMA foreign_keys = ON")
        return c
    db = Db(connect, "sqlite", (sqlite3.IntegrityError,))
    apply_schema(db)
    return db


def lakebase_db(instance: str | None = None, database: str | None = None) -> Db:
    """psycopg to Lakebase, a fresh OAuth token per connection (tokens are short-lived)."""
    import psycopg
    from databricks.sdk import WorkspaceClient
    w = WorkspaceClient()
    instance = instance or os.environ.get("QUOTE_AI_LAKEBASE_INSTANCE", "quote-ai-db")
    host = os.environ.get("PGHOST") or w.database.get_database_instance(instance).read_write_dns
    user = os.environ.get("PGUSER") or w.current_user.me().user_name
    dbname = database or os.environ.get("PGDATABASE", "quote_ai")

    def connect():
        token = w.database.generate_database_credential(request_id=str(uuid.uuid4()),
                                                        instance_names=[instance]).token
        return psycopg.connect(host=host, dbname=dbname, user=user, password=token, sslmode="require")
    return Db(connect, "postgres", (psycopg.errors.IntegrityError,))
