"""AccessGUDID v3 Device Lookup (AI-19): a recorded response parsed (no live call in CI), the cache per DI, the
transient errors, and the governed route through the UC function."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quote_ai.agents.reliability import RateLimited, ServiceUnavailable
from quote_ai.gudid import api_client as g

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "accessgudid_device_lookup.json").read_text())
T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)


def test_parses_a_saved_v3_lookup_response():
    rec = g.parse_lookup(RECORDED, T0)
    assert rec["primary_di"] == "00812345000000" and rec["brand_name"] == "Demo Nitrile Exam Glove A"
    assert rec["device_description"].startswith("Nitrile exam glove")
    assert rec["labeled_contains_nrl"] is False and rec["labeled_no_nrl"] is True
    assert rec["commercial_distribution"] == "In Commercial Distribution" and rec["in_commercial_distribution"]
    assert (rec["public_version_number"], rec["public_version_date"]) == (3, "2026-11-13")
    assert rec["source"] == "accessgudid_live" and rec["retrieved_at"].startswith("2026-11-14T09:00")


def test_an_empty_response_is_not_found():
    with pytest.raises(g.DeviceNotFound):
        g.parse_lookup({"gudid": {}})
    with pytest.raises(g.DeviceNotFound):
        g.lookup_path("not-a-di")
    assert g.lookup_path("00812345000000") == "/devices/lookup.json?di=00812345000000"


def test_the_cache_makes_one_call_per_device_a_day():
    now = [T0]
    calls = []

    def fetch(di, timeout):
        calls.append(di)
        return RECORDED
    live = g.LiveGudid(fetch, clock=lambda: now[0])
    first = live.lookup("00812345000000")
    again = live.lookup("00812345000000")
    assert live.calls == 1 and again["cached"] and again["public_version_number"] == first["public_version_number"]
    now[0] = T0 + timedelta(hours=25)
    live.lookup("00812345000000")
    assert live.calls == 2 and calls == ["00812345000000"] * 2


@pytest.mark.parametrize("status, error", [(429, RateLimited), (503, ServiceUnavailable), (500, ServiceUnavailable),
                                           (404, g.DeviceNotFound)])
def test_http_errors_map_to_transient_or_not_found(status, error):
    session = SimpleNamespace(get=lambda url, timeout: SimpleNamespace(status_code=status, json=lambda: {},
                                                                       raise_for_status=lambda: None))
    with pytest.raises(error):
        g.http_fetch("00812345000000", session=session)


def test_the_governed_route_reads_the_uc_function():
    sent = {}

    class Sql:
        def query(self, sql, params):
            sent.update(sql=sql, params=params)
            return [{"r": json.dumps(RECORDED)}]
    body = g.uc_fetch(Sql(), "cat.quote_ai_ai")("00812345000000", 3)
    assert sent["sql"] == "SELECT cat.quote_ai_ai.gudid_live_lookup(:di) AS r" and body == RECORDED

    class Down:
        def query(self, sql, params):
            raise RuntimeError("HTTP request failed with status 503")
    with pytest.raises(ServiceUnavailable):
        g.uc_fetch(Down(), "cat.quote_ai_ai")("00812345000000", 3)
