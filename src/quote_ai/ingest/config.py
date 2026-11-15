"""The ingestion config: config/ingest/sources.yml, checked by this schema in CI.

The config says WHAT to load: source, format, location, target, keys, schedule. The engine knows HOW. A source can land
several files (one GUDID release holds a file per entity), so a source has a list of tables, each with its own keys.

Strict on purpose (extra="forbid"): a misspelt field fails the pull request instead of being silently ignored. The
config stays declarative; a truly odd source gets code behind the same interface, not a new flag for every exception.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "config" / "ingest" / "sources.yml"

# Ingestion writes bronze only. Silver and gold are built from bronze (AI-03), never loaded directly.
BRONZE_SCHEMAS = {"gudid_bronze", "sales_bronze"}
# Landing code that the config may name. Anything else needs code reviewed into this list, not a config flag.
GENERATORS = {"dbxlab_synthetic"}

IDENT = re.compile(r"^[a-z][a-z0-9_]*$")
TARGET = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Expectation(Strict):
    rule: str = Field(min_length=1)
    action: Literal["warn", "drop", "fail"] = "warn"


class ApiLimits(Strict):
    """Rate limits are not published by AccessGUDID: be gentle, and cap every run."""
    max_rps: float = Field(2, gt=0, le=5)
    retries: int = Field(5, ge=0, le=10)
    backoff_s: float = Field(2, gt=0, le=60)
    max_requests_per_run: int = Field(2000, ge=1, le=20000)
    timeout_s: float = Field(30, gt=0, le=120)
    partitions: int = Field(2, ge=1, le=16)


class IdsFrom(Strict):
    """Which identifiers the API looks up: those that changed in a bronze table since the last successful run."""
    table: str
    column: str
    param: Literal["di", "record_key"] = "di"
    changed_since: Literal["watermark", "all"] = "watermark"

    @field_validator("table")
    @classmethod
    def _table(cls, v: str) -> str:
        if not TARGET.match(v):
            raise ValueError(f"ids_from.table must be schema.table, got {v!r}")
        return v

    @field_validator("column")
    @classmethod
    def _column(cls, v: str) -> str:
        if not IDENT.match(v):
            raise ValueError(f"ids_from.column must be a snake_case column, got {v!r}")
        return v


class Download(Strict):
    """Where the downloader fetches release files. url is a template: {date:%Y%m%d} is the release date."""
    url: str
    kind: Literal["full", "daily"]
    lookback_days: int = Field(7, ge=1, le=60)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        if not v.startswith("https://") or "{date" not in v:
            raise ValueError("download.url must be https:// and contain {date:...}")
        return v

    def url_for(self, d: date) -> str:
        return self.url.format(date=d)


class Table(Strict):
    target: str
    pattern: str | None = None
    keys: list[str] = Field(min_length=1)
    sequence_by: str = "_release_date"
    expectations: dict[str, Expectation] = {}
    schema_evolution: Literal["add_new_columns", "rescue"] = "add_new_columns"
    options: dict[str, str] = {}
    comment: str | None = None
    tags: dict[str, str] = {}

    @field_validator("target")
    @classmethod
    def _target(cls, v: str) -> str:
        if not TARGET.match(v):
            raise ValueError(f"target must be schema.table in snake_case, got {v!r}")
        if v.split(".")[0] not in BRONZE_SCHEMAS:
            raise ValueError(f"target {v!r}: ingestion writes only to {sorted(BRONZE_SCHEMAS)}")
        return v

    @field_validator("keys")
    @classmethod
    def _keys(cls, v: list[str]) -> list[str]:
        bad = [k for k in v if not IDENT.match(k)]
        if bad:
            raise ValueError(f"keys must be snake_case columns, got {bad}")
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate keys {v}")
        return v

    @field_validator("expectations")
    @classmethod
    def _expectation_names(cls, v: dict[str, Expectation]) -> dict[str, Expectation]:
        bad = [k for k in v if not IDENT.match(k)]
        if bad:
            raise ValueError(f"expectation names must be snake_case, got {bad}")
        return v

    @property
    def schema_name(self) -> str:
        return self.target.split(".")[0]

    @property
    def table_name(self) -> str:
        return self.target.split(".")[1]


class Source(Strict):
    name: str
    type: Literal["file", "api"]
    description: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    enabled: bool = True
    schedule: Literal["once", "daily", "weekly", "on_demand"]
    tables: list[Table] = Field(min_length=1)
    # file sources
    volume: str | None = None          # schema.volume, e.g. gudid_bronze.releases
    landing: str | None = None         # folder in the volume; files land in <landing>/<yyyymmdd>/
    download: Download | None = None   # fetched by the downloader ...
    generator: str | None = None       # ... or written by named landing code; neither = dropped by hand
    format: Literal["csv", "xml", "json", "parquet"] | None = None
    options: dict[str, str] = {}
    # api sources
    base_url: str | None = None
    endpoint: str | None = None
    ids_from: IdsFrom | None = None
    api: ApiLimits | None = None

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not IDENT.match(v):
            raise ValueError(f"source name must be snake_case, got {v!r}")
        return v

    @model_validator(mode="after")
    def _fits_type(self) -> "Source":
        n = self.name
        if self.type == "file":
            missing = [f for f in ("volume", "landing", "format") if not getattr(self, f)]
            if missing:
                raise ValueError(f"{n}: a file source needs {missing}")
            if not TARGET.match(self.volume):
                raise ValueError(f"{n}: volume must be schema.volume, got {self.volume!r}")
            if self.download and self.generator:
                raise ValueError(f"{n}: download or generator, not both")
            if self.generator and self.generator not in GENERATORS:
                raise ValueError(f"{n}: unknown generator {self.generator!r}; known: {sorted(GENERATORS)}")
            if any(not t.pattern for t in self.tables):
                raise ValueError(f"{n}: every table of a file source needs a pattern")
            api_fields = [f for f in ("base_url", "endpoint", "ids_from", "api") if getattr(self, f)]
            if api_fields:
                raise ValueError(f"{n}: a file source can't have {api_fields}")
        else:
            missing = [f for f in ("base_url", "endpoint", "ids_from") if not getattr(self, f)]
            if missing:
                raise ValueError(f"{n}: an api source needs {missing}")
            if not self.base_url.startswith("https://"):
                raise ValueError(f"{n}: base_url must be https://")
            if len(self.tables) != 1:
                raise ValueError(f"{n}: an api source writes exactly one table")
            file_fields = [f for f in ("volume", "landing", "download", "generator", "format") if getattr(self, f)]
            if file_fields or any(t.pattern for t in self.tables):
                raise ValueError(f"{n}: an api source can't have {file_fields or ['pattern']}")
            if self.api is None:
                self.api = ApiLimits()
        return self


class IngestConfig(Strict):
    version: Literal[1] = 1
    sources: list[Source] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "IngestConfig":
        names = [s.name for s in self.sources]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate source names {dupes}")
        owner: dict[str, str] = {}
        for s in self.sources:
            for t in s.tables:
                if owner.setdefault(t.target, s.name) != s.name:
                    raise ValueError(f"{t.target} is written by {owner[t.target]} and {s.name}: one source per "
                                     "bronze table (merge sources in silver)")
                if [x.target for x in s.tables].count(t.target) > 1:
                    raise ValueError(f"{s.name} writes {t.target} twice")
        file_targets = {t.target for s in self.sources if s.type == "file" for t in s.tables}
        for s in self.sources:
            if s.ids_from and s.ids_from.table not in file_targets:
                raise ValueError(f"{s.name}: ids_from.table {s.ids_from.table} is not loaded by a file source")
        return self

    def source(self, name: str) -> Source:
        for s in self.sources:
            if s.name == name:
                return s
        raise KeyError(f"no source {name!r} in the ingestion config")

    def enabled(self, type: str | None = None, schedule: str | None = None) -> list[Source]:
        return [s for s in self.sources if s.enabled and (type is None or s.type == type)
                and (schedule is None or s.schedule == schedule)]

    def schemas(self) -> list[str]:
        """Every schema the config touches: targets and volumes."""
        out = {t.schema_name for s in self.sources for t in s.tables}
        out |= {s.volume.split(".")[0] for s in self.sources if s.volume}
        return sorted(out)

    def volumes(self) -> list[str]:
        return sorted({s.volume for s in self.sources if s.volume})


def load_config(path: str | Path | None = None) -> IngestConfig:
    return IngestConfig.model_validate(yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8")))


def config_hash(cfg: IngestConfig) -> str:
    """The same config gives the same hash, however the YAML is formatted: the run log records which config ran."""
    canonical = json.dumps(cfg.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
