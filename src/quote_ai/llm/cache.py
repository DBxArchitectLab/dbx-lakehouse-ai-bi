"""A cache for model calls on unchanged inputs (AI-23, experiment E4): the same GUDID record, read by the same prompt
version on the same model, gives the same answer, so it is asked once.

The key is the kind of call, a version and the payload:
  cache_key(kind, payload, version)   sha256 of "kind|version|payload as sorted JSON"
  version                             prompt version + model service + the GUDID publicVersionNumber of the record,
                                      so a new record version, a new prompt or a new model each miss the cache
What is never cached: anything with a price, stock or an account in it (NEVER_CACHED kinds, NEVER_CACHED_FIELDS in
the payload). Those change by the hour or depend on who asks; a stale or someone else's answer is worse than a slow
one. cacheable() refuses them, and LlmCache.get_or_call calls the model every time for them.

  InMemoryCacheStore   tests, the local demo, the experiment's second run
  SqlCacheStore        <catalog>.quote_ai_ai.llm_cache (llm_cache.sql), through a SQL warehouse
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NEVER_CACHED = frozenset({"price", "pricing", "price_quote", "stock", "fulfilment", "account", "win_probability",
                          "ask_genie", "chat"})
NEVER_CACHED_FIELDS = frozenset({"price", "unit_price", "list_price", "cost", "stock", "available", "inventory",
                                 "account", "account_id", "accounts", "rep", "user"})
DDL = Path(__file__).with_name("llm_cache.sql")


class NotCacheable(ValueError):
    """A call whose answer depends on price, stock or who asks."""


def _fields(payload: Any) -> set[str]:
    if isinstance(payload, dict):
        return set(payload) | {k for v in payload.values() for k in _fields(v)}
    if isinstance(payload, (list, tuple)):
        return {k for v in payload for k in _fields(v)}
    return set()


def cacheable(kind: str, payload: Any) -> bool:
    return kind not in NEVER_CACHED and not (_fields(payload) & NEVER_CACHED_FIELDS)


def version_of(prompt_version: str, model: str, public_version_number: int | str | None = None) -> str:
    """The version part of the key: a change to any of the three is a miss."""
    return f"{prompt_version}|{model}|gudid:{public_version_number if public_version_number is not None else '-'}"


def cache_key(kind: str, payload: Any, version: str) -> str:
    if not cacheable(kind, payload):
        raise NotCacheable(f"{kind}: calls with price, stock or account data are never cached")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{kind}|{version}|{body}".encode()).hexdigest()


class InMemoryCacheStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def get(self, key: str) -> Any | None:
        row = self.rows.get(key)
        return None if row is None else row["value"]

    def put(self, key: str, kind: str, version: str, value: Any) -> None:
        self.rows[key] = {"kind": kind, "version": version, "value": value,
                          "created_at": datetime.now(timezone.utc)}


class SqlCacheStore:
    """Rows in {ai}.llm_cache. Verify before recording: MERGE with named parameters on the SQL warehouse."""

    def __init__(self, sql, ai_schema: str) -> None:
        self.sql, self.table = sql, f"{ai_schema}.llm_cache"

    def get(self, key: str) -> Any | None:
        rows = self.sql.query(f"SELECT value FROM {self.table} WHERE cache_key = :k", {"k": key})
        return json.loads(rows[0]["value"]) if rows else None

    def put(self, key: str, kind: str, version: str, value: Any) -> None:
        self.sql.execute(f"MERGE INTO {self.table} t USING (SELECT :k AS cache_key) s ON t.cache_key = s.cache_key "
                         f"WHEN NOT MATCHED THEN INSERT (cache_key, kind, version, value, created_at) "
                         f"VALUES (:k, :kind, :version, :value, current_timestamp())",
                         {"k": key, "kind": kind, "version": version, "value": json.dumps(value, default=str)})


class LlmCache:
    """get_or_call: the cached answer when the key is there, else the call, stored. Counts hits and misses (the
    experiment's cost on a second run)."""

    def __init__(self, store=None) -> None:
        self.store = store or InMemoryCacheStore()
        self.hits = self.misses = self.bypassed = 0

    def get_or_call(self, kind: str, payload: Any, version: str, call: Callable[[], Any]) -> Any:
        if not cacheable(kind, payload):
            self.bypassed += 1
            return call()
        key = cache_key(kind, payload, version)
        hit = self.store.get(key)
        if hit is not None:
            self.hits += 1
            return hit
        self.misses += 1
        value = call()
        self.store.put(key, kind, version, value)
        return value
