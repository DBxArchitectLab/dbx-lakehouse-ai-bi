"""The ingestion config: the repo's config is valid, and every bad entry in the Ingestion suite is refused in CI."""
from datetime import date
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from quote_ai.ingest.config import IngestConfig, Source, config_hash, load_config

ROOT = Path(__file__).resolve().parents[2]
BAD = sorted((ROOT / "evaluation" / "v1" / "ingestion" / "bad_configs").glob("*.yml"))


def test_repo_config_is_valid():
    cfg = load_config(ROOT / "config" / "ingest" / "sources.yml")
    assert {s.name for s in cfg.sources} == {"gudid_full_release", "gudid_daily_updates",
                                             "gudid_api_device_history", "dbxlab_synthetic"}
    assert cfg.source("gudid_api_device_history").api.max_requests_per_run == 2000
    assert cfg.schemas() == ["gudid_bronze", "sales_bronze"]


def test_every_table_has_keys_and_every_file_table_a_pattern():
    cfg = load_config()
    for s in cfg.sources:
        for t in s.tables:
            assert t.keys
            assert bool(t.pattern) == (s.type == "file")


@pytest.mark.parametrize("path", BAD, ids=[p.stem for p in BAD])
def test_bad_config_is_refused(path):
    expect = path.read_text(encoding="utf-8").splitlines()[0].removeprefix("# expect:").strip()
    with pytest.raises(ValidationError, match=expect.replace("[", r"\[").replace("]", r"\]")):
        IngestConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def test_the_suite_has_bad_configs():
    assert len(BAD) >= 6


def test_api_limits_default_when_omitted():
    raw = yaml.safe_load((ROOT / "config" / "ingest" / "sources.yml").read_text(encoding="utf-8"))
    api = next(s for s in raw["sources"] if s["type"] == "api")
    del api["api"]
    cfg = IngestConfig.model_validate(raw)
    assert cfg.source(api["name"]).api.max_rps == 2


def test_hash_ignores_formatting_but_not_content():
    text = (ROOT / "config" / "ingest" / "sources.yml").read_text(encoding="utf-8")
    a = IngestConfig.model_validate(yaml.safe_load(text))
    b = IngestConfig.model_validate(yaml.safe_load(yaml.safe_dump(yaml.safe_load(text), sort_keys=True)))
    assert config_hash(a) == config_hash(b)
    c = a.model_copy(deep=True)
    c.sources[0].tables[0].keys = ["primary_di", "public_version_number"]
    assert config_hash(c) != config_hash(a)


def test_download_url_template():
    s = load_config().source("gudid_daily_updates")
    assert s.download.url_for(date(2026, 11, 12)).endswith("/gudid_daily_update_20261112.zip")


def test_a_generator_must_be_known():
    with pytest.raises(ValidationError, match="unknown generator"):
        Source(name="x", type="file", description="x", owner="o", schedule="daily", volume="sales_bronze.landing",
               landing="x", format="csv", generator="run_anything",
               tables=[{"target": "sales_bronze.x_raw", "pattern": "*.csv", "keys": ["id"]}])
