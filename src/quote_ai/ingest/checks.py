"""The Ingestion suite on bronze ("How we know it's correct", AI-02): each check is a query that must return 0.

  one_row_per_key     after any replay, exactly one row per key
  load_id_present     every row carries its _load_id
  load_id_logged      every _load_id belongs to a succeeded (or partial) run in ops.ingestion_log

The config's own checks (an entry without keys, a file source without a pattern, ...) run in CI: tests/quote_ai and
evaluation/v1/ingestion.
"""
from __future__ import annotations

from dataclasses import dataclass

from quote_ai.ingest.config import IngestConfig
from quote_ai.ingest.lineage import qualified


@dataclass(frozen=True)
class Check:
    name: str
    table: str
    sql: str            # returns one number; the check passes when it is 0


def bronze_checks(cfg: IngestConfig, table_fqn, ops_fqn: str) -> list[Check]:
    """table_fqn maps a logical target to its physical name (engine.Names.table)."""
    out, seen = [], set()
    for s in cfg.enabled():
        for t in s.tables:
            if t.target in seen:
                continue
            seen.add(t.target)
            tbl = qualified(table_fqn(t.target))
            keys = ", ".join(qualified(k) for k in t.keys)
            out += [
                Check("one_row_per_key", t.target,
                      f"SELECT count(*) FROM (SELECT {keys} FROM {tbl} GROUP BY {keys} HAVING count(*) > 1)"),
                Check("load_id_present", t.target, f"SELECT count(*) FROM {tbl} WHERE `_load_id` IS NULL"),
                Check("load_id_logged", t.target,
                      f"SELECT count(DISTINCT b.`_load_id`) FROM {tbl} b LEFT ANTI JOIN "
                      f"(SELECT explode(load_ids) AS load_id FROM {qualified(ops_fqn)}.`ingestion_log` "
                      f"WHERE status IN ('succeeded', 'partial')) l ON b.`_load_id` = l.load_id"),
            ]
    return out
