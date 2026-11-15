"""The landing zone: immutable and replayable, every file in the manifest once, and lineage that survives a replay."""
import io
import zipfile
from datetime import date

import pytest

from quote_ai.ingest.config import load_config
from quote_ai.ingest.download import LandingConflict, days_to_fetch, land_release, land_source
from quote_ai.ingest.lineage import (load_id_for, merge_sql, normalized_columns, qualified, release_date_from_path,
                                     snake_case)
from quote_ai.ingest.log import InMemoryLog
from quote_ai.ingest.synthetic import GENERATORS

CFG = load_config()
DAILY = CFG.source("gudid_daily_updates")
FULL = CFG.source("gudid_full_release")
D = date(2026, 11, 12)


def zip_bytes(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


class Resp:
    def __init__(self, body):
        self.body = body

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]


class FakeHttp:
    """Serves zips by URL; any other URL is a 404 (no release that day)."""

    def __init__(self, files_by_url):
        self.files, self.urls = files_by_url, []

    def get(self, url, params=None, stream=False, allow_404=True):
        self.urls.append(url)
        return Resp(self.files[url]) if url in self.files else None


FULL_ZIP = zip_bytes({"AccessGUDID/device.txt": "PrimaryDI|BrandName\n00888|Glove A\n00999|Glove B\n",
                      "AccessGUDID/identifiers.txt": "PrimaryDI|DeviceId\n00888|00888\n"})


def test_a_release_lands_as_published_and_in_the_manifest(tmp_path):
    log, url = InMemoryLog(), FULL.download.url_for(D)
    recs = land_release(FakeHttp({url: FULL_ZIP}), FULL, D, tmp_path, log, job_run_id="run-1")
    folder = tmp_path / "full" / "20261112"
    assert sorted(p.name for p in folder.iterdir()) == ["AccessGUDID_Delimited_Full_Release_20261112.zip",
                                                       "device.txt", "identifiers.txt"]      # folders flattened
    device = next(r for r in recs if r.path.endswith("device.txt"))
    assert device.row_count == 2 and device.load_id == "gudid_full_release:20261112"
    assert device.source_url.endswith("!AccessGUDID/device.txt") and len(device.sha256) == 64
    assert len(log.file_rows) == 3 and all(f.job_run_id == "run-1" for f in log.file_rows)


def test_landing_the_same_release_twice_is_recorded_once(tmp_path):
    log, url = InMemoryLog(), FULL.download.url_for(D)
    http = FakeHttp({url: FULL_ZIP})
    land_release(http, FULL, D, tmp_path, log)
    land_release(http, FULL, D, tmp_path, log)
    assert len(log.file_rows) == 3


def test_a_republished_file_with_different_content_is_refused(tmp_path):
    log, url = InMemoryLog(), FULL.download.url_for(D)
    land_release(FakeHttp({url: FULL_ZIP}), FULL, D, tmp_path, log)
    changed = zip_bytes({"device.txt": "PrimaryDI\n00777\n"})
    with pytest.raises(LandingConflict, match="different content"):
        land_release(FakeHttp({url: changed}), FULL, D, tmp_path, log)
    assert (tmp_path / "full" / "20261112" / "device.txt").read_text().count("\n") == 3   # the original is kept


def test_a_zip_slip_path_is_refused(tmp_path):
    url = FULL.download.url_for(D)
    with pytest.raises(LandingConflict, match="unsafe path"):
        land_release(FakeHttp({url: zip_bytes({"../evil.txt": "x"})}), FULL, D, tmp_path, InMemoryLog())


def test_daily_fetches_only_missing_days_and_skips_days_without_a_release(tmp_path):
    log = InMemoryLog()
    http = FakeHttp({DAILY.download.url_for(date(2026, 11, 10)): zip_bytes({"d.xml": "<device/>"}),
                     DAILY.download.url_for(date(2026, 11, 12)): zip_bytes({"d.xml": "<device/>"})})
    landed = land_source(http, DAILY, tmp_path, log, today=date(2026, 11, 12))
    assert {r.load_id for r in landed} == {"gudid_daily_updates:20261110", "gudid_daily_updates:20261112"}
    assert len(http.urls) == 8                                   # 7-day lookback: 11-05 to 11-12; 404s skipped
    assert days_to_fetch(DAILY, log, date(2026, 11, 14)) == [date(2026, 11, 13), date(2026, 11, 14)]


def test_a_full_release_needs_its_date(tmp_path):
    with pytest.raises(ValueError, match="release date"):
        land_source(FakeHttp({}), FULL, tmp_path, InMemoryLog(), today=D)


def test_synthetic_files_are_deterministic(tmp_path):
    s, log = CFG.source("dbxlab_synthetic"), InMemoryLog()
    first = land_source(None, s, tmp_path, log, today=D, generators=GENERATORS)
    again = land_source(None, s, tmp_path, log, today=D, generators=GENERATORS)
    assert [r.sha256 for r in first] == [r.sha256 for r in again] and len(log.file_rows) == 3
    inventory = (tmp_path / "dbxlab" / "20261112" / "inventory_20261112.csv").read_text().splitlines()
    assert "INV-20261112,GLOVE-C-M,65,0,7,500,14,2026-11-12" in inventory    # Glove C is short, as in the fixtures


# ------------------------------------------------------------------ lineage

@pytest.mark.parametrize("raw, snake", [("PrimaryDI", "primary_di"), ("publicVersionNumber", "public_version_number"),
                                        ("MRISafetyStatus", "mri_safety_status"), ("public-device", "public_device"),
                                        ("DUNSNumber", "duns_number"), ("brand_name", "brand_name")])
def test_columns_are_normalized_to_snake_case(raw, snake):
    assert snake_case(raw) == snake


def test_engine_columns_are_kept_and_collisions_refused():
    assert normalized_columns(["PrimaryDI", "_rescued_data"]) == ["primary_di", "_rescued_data"]
    with pytest.raises(ValueError, match="collide"):
        normalized_columns(["PrimaryDI", "primary_di"])


def test_release_date_and_load_id_come_from_the_landing_folder():
    p = "/Volumes/c/quote_ai_gudid_bronze/quote_ai_releases/daily/20261112/gudid_daily_update_20261112.xml"
    assert release_date_from_path(p) == D
    assert load_id_for("gudid_daily_updates", "20261112") == "gudid_daily_updates:20261112"


def test_merge_updates_by_key_and_never_lets_an_older_version_win():
    sql = merge_sql("c.quote_ai_gudid_bronze.api_device_history", "c.quote_ai_gudid_bronze.stage",
                    ["public_device_record_key", "public_version_number"])
    assert "ON t.`public_device_record_key` <=> s.`public_device_record_key` AND" in sql
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY `public_device_record_key`, `public_version_number`" in sql
    assert "WHEN MATCHED AND s.`_release_date` >= t.`_release_date` THEN UPDATE SET *" in sql
    assert "WHEN NOT MATCHED THEN INSERT *" in sql


def test_identifiers_are_checked_before_they_reach_sql():
    with pytest.raises(ValueError):
        qualified("c.s.t; DROP TABLE x")


def test_files_dropped_by_hand_are_recorded_once(tmp_path):
    """A source with no download and no generator: new files in <landing>/<yyyymmdd>/ get manifest rows (and so a
    logged load ID); files already recorded, part files and non-date folders are skipped."""
    from quote_ai.ingest.config import Source
    s = Source(name="dbxlab_supplier_price_list", type="file", description="supplier price list, dropped by hand",
               owner="sales-ops", schedule="on_demand", volume="quote_ai_sales_bronze.quote_ai_landing",
               landing="supplier", format="csv",
               tables=[{"target": "quote_ai_sales_bronze.supplier_price_raw", "pattern": "*.csv",
                        "keys": ["supplier_id", "sku_id"]}])
    day = tmp_path / "supplier" / "20261114"
    day.mkdir(parents=True)
    (day / "prices.csv").write_text("supplier_id,sku_id,price\nS1,GLOVE-A-M,6.10\n")
    (day / "prices.csv.part").write_text("half")
    (tmp_path / "supplier" / "notes").mkdir()
    log = InMemoryLog()
    first = land_source(None, s, tmp_path, log, today=D)
    assert [(r.load_id, r.row_count, r.kind) for r in first] == \
        [("dbxlab_supplier_price_list:20261114", 1, "manual")]
    assert land_source(None, s, tmp_path, log, today=D) == []          # already in the manifest
