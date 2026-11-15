"""HTTP manners and the AccessGUDID client: throttled and retried, not failed; and a clear error when it must fail."""
import json

import pytest

from quote_ai.ingest.api_source import split_ids
from quote_ai.ingest.gudid_client import HISTORY_FIELDS, GudidClient
from quote_ai.ingest.http import Http, HttpError, RateLimiter

BASE, ENDPOINT = "https://accessgudid.nlm.nih.gov/api/v3/", "devices/history.json"

HISTORY = {"history": {
    "2": {"primary_di": "00888", "publicDeviceRecordKey": "rk-1", "publicVersionNumber": 2,
          "publicVersionStatus": "Update", "publicVersionDate": "2026-11-12", "creationDate": "2024-01-03",
          "xml": "<device/>"},
    "1": {"primary_di": "00888", "publicDeviceRecordKey": "rk-1", "publicVersionNumber": 1,
          "publicVersionStatus": "New", "publicVersionDate": "2024-01-03", "creationDate": "2024-01-03",
          "xml": "<device/>"},
}}


class Resp:
    def __init__(self, status, body=None, headers=None):
        self.status_code, self._body, self.headers = status, body, headers or {}

    def json(self):
        return self._body


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def get(self, url, params=None, timeout=None, stream=False):
        self.calls.append((url, params))
        return self.responses.pop(0)


class FakeTime:
    def __init__(self):
        self.now, self.slept = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.now += s


def client(responses, retries=5, max_rps=1000):
    t = FakeTime()
    session = FakeSession(responses)
    http = Http(max_rps=max_rps, retries=retries, backoff_s=2, session=session, clock=t.clock, sleep=t.sleep)
    return GudidClient(BASE, ENDPOINT, http), session, t


def test_throttled_twice_then_ok():
    c, session, t = client([Resp(429, headers={"Retry-After": "3"}), Resp(503), Resp(200, HISTORY)])
    rows = list(c.history("record_key", "rk-1"))
    assert len(session.calls) == 3
    assert t.slept == [3.0, 4.0]             # Retry-After honoured; then backoff 2 * 2**1
    assert [r[HISTORY_FIELDS.index("public_version_number")] for r in rows] == ["1", "2"]   # oldest first
    assert c.http.retried == 2


def test_gives_up_naming_the_record():
    c, session, _ = client([Resp(500)] * 6)
    with pytest.raises(HttpError, match="record_key.*rk-9.*gave up after 6 tries"):
        list(c.history("record_key", "rk-9"))
    assert len(session.calls) == 6


def test_a_client_error_is_not_retried():
    c, session, _ = client([Resp(400)])
    with pytest.raises(HttpError, match="HTTP 400"):
        list(c.history("di", "x"))
    assert len(session.calls) == 1


def test_unknown_record_yields_nothing():
    c, _, _ = client([Resp(404)])
    assert list(c.history("di", "nope")) == []


def test_a_row_keeps_the_version_as_returned_and_the_request():
    c, _, _ = client([Resp(200, HISTORY)])
    row = dict(zip(HISTORY_FIELDS, next(c.history("record_key", "rk-1"))))
    assert row["primary_di"] == "00888" and row["public_device_record_key"] == "rk-1"
    assert row["request"].endswith("devices/history.json?record_key=rk-1")
    assert json.loads(row["payload"])["xml"] == "<device/>"


def test_rate_limiter_spaces_requests():
    t = FakeTime()
    limiter = RateLimiter(2, t.clock, t.sleep)
    for _ in range(3):
        limiter.wait()
    assert t.slept == [0.5, 0.5]


def test_ids_are_split_across_partitions():
    chunks = split_ids([str(i) for i in range(5)], 2)
    assert chunks == [["0", "2", "4"], ["1", "3"]]
    assert split_ids(["a"], 4) == [["a"]]
    assert split_ids([], 2) == []
