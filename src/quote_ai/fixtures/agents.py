"""Season 5 helpers (AI-18 to AI-21): the Lakeside quote at each step, the demo tools, a scripted model client, and a
Delta quote store on SQLite. Used by the unit tests (tests/quote_ai/agent_fixtures.py re-exports them) and by the
Reliability and Agent workflow suites (evaluation/quote_ai/season5.py), which must run without pytest (AI-22)."""
from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

from quote_ai.fixtures import lakeside as fx

NO_WAIT = {"sleep": lambda s: None, "timeout": 2.0}

# The Delta quote tables (AI-07) as SQLite DDL: DeltaQuoteRepository runs the same statements on it.
DDL = """
CREATE TABLE quote (quote_id TEXT PRIMARY KEY, version INT, revision INT, account TEXT, rep TEXT, state TEXT,
  request_text TEXT, requirements TEXT, product_id TEXT, total REAL, price_method TEXT, doc TEXT,
  created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE quote_line (quote_id TEXT, version INT, line_no INT, size TEXT, quantity INT, unit TEXT, sku_id TEXT,
  unit_price REAL, list_price REAL, price_book_id TEXT);
CREATE TABLE quote_event (quote_id TEXT, quote_version INT, revision INT, action TEXT, from_state TEXT,
  to_state TEXT, actor TEXT, at TIMESTAMP, trace_id TEXT, idempotency_key TEXT, detail TEXT);
"""


class SqliteSql:
    """The SqlExecutor interface on SQLite (named :params, rowcount for UPDATE)."""

    def __init__(self) -> None:
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(DDL)
        self.lock = threading.Lock()
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        with self.lock:
            self.statements.append(sql)
            cur = self.db.execute(sql, {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                                        for k, v in (params or {}).items()})
            self.db.commit()
            return cur.rowcount

    def query(self, sql, params=None):
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params or {}).fetchall()]


def service(repo=None):
    from quote_ai.services import QuoteService
    from quote_ai.storage.repository import InMemoryQuoteRepository
    return QuoteService(repo or InMemoryQuoteRepository(), clock=lambda: fx.NOW)


def checks(q, letter, snapshot="ev-41"):
    """Glove <letter>'s eligibility and fulfilment for quote q, from the fixtures: what shortlist() takes."""
    from quote_ai.rules import eligibility, fulfilment
    p = fx.product(letter)
    elig = eligibility.check_product(p, q.requirements, fx.evidence(snapshot), snapshot)
    ful = fulfilment.check_product(p, q.lines, fx.stock(), q.requirements.delivery_days, "inv-2026-11-14")
    return p, elig, ful


def confirmed(svc=None):
    from quote_ai.agents import runtime
    svc = svc or service()
    return svc, runtime.demo_quote(svc)


def priced(svc=None, asked=None):
    """Lakeside on Glove A, PRICED by price_quote (optionally at an asked price)."""
    svc, q = confirmed(svc)
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))
    return svc, svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=asked)


def delta_with_quotes() -> SqliteSql:
    """Two quotes on the Delta store (AI-20's migration source): Lakeside PRICED on Glove A, Harbor Point a DRAFT."""
    from quote_ai.storage.delta_repository import DeltaQuoteRepository
    sql = SqliteSql()
    svc = service(DeltaQuoteRepository(sql, "main"))
    _, q = confirmed(svc)
    q = svc.shortlist(q.quote_id, q.revision, "rep.alice", *checks(q, "A"))
    svc.price(q.quote_id, q.revision, "rep.alice", "standard", fx.price_book(), asked_unit_price=8.00)
    svc.create("Harbor Point", "rep.bob", "50 boxes", fx.lakeside_r1(), quote_id="Q-B")
    return sql


def toolbox(quote=None, **deps):
    from quote_ai.agents import runtime
    from quote_ai.agents.tools import Toolbox
    return Toolbox(runtime.demo_deps(**deps), quote, "rep.alice")


def agent(**deps):
    from quote_ai.agents import runtime
    from quote_ai.agents.agent import QuoteAgent
    from quote_ai.agents.standin import StandInClient
    return QuoteAgent(deps=runtime.demo_deps(**deps), client=StandInClient(), endpoint="standin")


def user(text: str) -> dict:
    return {"role": "user", "content": text}


class ScriptedClient:
    """Plays a fixed list of model turns: each a list of (tool, args) calls, or a string (the answer)."""

    def __init__(self, turns: list) -> None:
        self.turns, self.calls = list(turns), 0

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kw):
        turn = self.turns[min(self.calls, len(self.turns) - 1)]
        self.calls += 1
        if isinstance(turn, str):
            msg = SimpleNamespace(content=turn, tool_calls=None)
        else:
            msg = SimpleNamespace(content=None, tool_calls=[
                SimpleNamespace(id=f"c{self.calls}-{i}", type="function",
                                function=SimpleNamespace(name=n, arguments=json.dumps(a)))
                for i, (n, a) in enumerate(turn)])
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10, total_tokens=110))
