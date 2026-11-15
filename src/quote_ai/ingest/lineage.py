"""Lineage and idempotency, in plain Python so it is tested without Spark.

Every bronze row carries _source, _file_or_request, _load_id, _ingested_at and _release_date. A load ID is
deterministic: the same release file (or the same API window) always gets the same load ID, so a replay is
recognisable and merges onto the rows it loaded before instead of adding to them.
"""
from __future__ import annotations

import re
from datetime import date, datetime

LINEAGE_COLUMNS = ["_source", "_file_or_request", "_load_id", "_ingested_at", "_release_date"]

_TAG = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def snake_case(name: str) -> str:
    """PrimaryDI -> primary_di, publicVersionNumber -> public_version_number, MRISafetyStatus -> mri_safety_status."""
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name.strip())
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    s = re.sub(r"[^0-9A-Za-z]+", "_", s).strip("_").lower()
    return s or "_col"


def normalized_columns(columns: list[str]) -> list[str]:
    """snake_case every column, keeping engine columns (_rescued_data, _metadata) and refusing collisions."""
    out = [c if c.startswith("_") else snake_case(c) for c in columns]
    dupes = sorted({c for c in out if out.count(c) > 1})
    if dupes:
        raise ValueError(f"columns collide after normalizing to snake_case: {dupes}")
    return out


def release_tag(d: date) -> str:
    return d.strftime("%Y%m%d")


def release_date_from_path(path: str) -> date | None:
    """The release date of a landed file: the last yyyymmdd in its path (the landing folder is <yyyymmdd>/)."""
    tags = _TAG.findall(path)
    return datetime.strptime(tags[-1], "%Y%m%d").date() if tags else None


def load_id_for(source: str, tag: str) -> str:
    """gudid_daily_updates:20261112. The same file or API window always has the same load ID."""
    return f"{source}:{tag}"


def _q(ident: str) -> str:
    if not _IDENT.match(ident):
        raise ValueError(f"not a plain identifier: {ident!r}")
    return f"`{ident}`"


def qualified(name: str) -> str:
    """catalog.schema.table, each part checked and quoted."""
    return ".".join(_q(p) for p in name.split("."))


def merge_sql(target: str, staging: str, keys: list[str], sequence_by: str = "_release_date") -> str:
    """MERGE the staging rows onto the target by key. A replay updates rows instead of adding them; an older version
    never overwrites a newer one; duplicates within one load keep the latest."""
    on = " AND ".join(f"t.{_q(k)} <=> s.{_q(k)}" for k in keys)
    part = ", ".join(_q(k) for k in keys)
    seq = _q(sequence_by)
    return (
        f"MERGE INTO {qualified(target)} AS t\n"
        f"USING (SELECT * FROM {qualified(staging)}\n"
        f"       QUALIFY ROW_NUMBER() OVER (PARTITION BY {part} ORDER BY {seq} DESC, `_ingested_at` DESC) = 1) AS s\n"
        f"ON {on}\n"
        f"WHEN MATCHED AND s.{seq} >= t.{seq} THEN UPDATE SET *\n"
        f"WHEN NOT MATCHED THEN INSERT *"
    )
