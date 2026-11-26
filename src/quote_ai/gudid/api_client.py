"""AccessGUDID API v3 Device Lookup, for ONE device (AI-19): the live record the agent uses to confirm a device is
current. Search, filters, analytics and eligibility keep using the lakehouse copy (daily, AI-02 / AI-03).

  parse_lookup(body)   the fields QuoteAI shows, from a saved or live v3 lookup response (unit-tested on a recorded
                       response: no live call in CI)
  LiveGudid            a cache per DI for 24 hours (AccessGUDID publishes no rate limit), so the same question twice
                       is one API call; calls counts the real ones
  uc_fetch             the governed route: the Unity Catalog function quote_ai_ai.gudid_live_lookup through the
                       quote_ai_accessgudid connection (no secrets in code, every call under a grant)
  http_fetch           a direct call (notebooks, local checks only)

Errors: 429 RateLimited, 5xx ServiceUnavailable, a slow call TimeoutError (all transient: reliability.py retries
them); an unknown DI is DeviceNotFound, an answer. Verify before recording: the lookup path
/devices/lookup.json?di=..., the response shape {"gudid": {"device": {...}}} and its field names, the terms of use.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from quote_ai.agents.reliability import RateLimited, ServiceUnavailable

BASE_URL = "https://accessgudid.nlm.nih.gov/api/v3"
LOOKUP_PATH = "/devices/lookup.json"
CACHE_TTL = timedelta(hours=24)


class DeviceNotFound(LookupError):
    pass


def lookup_path(di: str) -> str:
    di = str(di).strip()
    if not di.isdigit() or not 8 <= len(di) <= 23:
        raise DeviceNotFound(f"not a device identifier: {di!r}")
    return f"{LOOKUP_PATH}?di={di}"


def _bool(v) -> bool | None:
    if isinstance(v, bool) or v is None:
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def parse_lookup(body: dict, retrieved_at: datetime | None = None) -> dict:
    """The fields QuoteAI uses from a v3 Device Lookup response."""
    d = ((body or {}).get("gudid") or {}).get("device")
    if not d:
        raise DeviceNotFound("no device in the AccessGUDID response")
    ids = ((d.get("identifiers") or {}).get("identifier")) or []
    ids = ids if isinstance(ids, list) else [ids]
    primary = next((i.get("deviceId") for i in ids if i.get("deviceIdType") == "Primary"),
                   ids[0].get("deviceId") if ids else None)
    status = d.get("deviceCommDistributionStatus")
    return {"primary_di": primary, "brand_name": d.get("brandName"), "company_name": d.get("companyName"),
            "device_description": d.get("deviceDescription"), "version_model_number": d.get("versionModelNumber"),
            "catalog_number": d.get("catalogNumber"),
            "labeled_contains_nrl": _bool(d.get("labeledContainsNRL")), "labeled_no_nrl": _bool(d.get("labeledNoNRL")),
            "commercial_distribution": status,
            "in_commercial_distribution": status == "In Commercial Distribution",
            "public_version_number": d.get("publicVersionNumber"), "public_version_date": d.get("publicVersionDate"),
            "record_status": d.get("deviceRecordStatus"),
            "retrieved_at": (retrieved_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
            "source": "accessgudid_live"}


class LiveGudid:
    """fetch(di, timeout) -> the response body (dict). The cache is per DI: one API call per device a day."""

    def __init__(self, fetch: Callable[[str, float], dict], clock: Callable[[], datetime] | None = None,
                 ttl: timedelta = CACHE_TTL) -> None:
        self.fetch, self.ttl = fetch, ttl
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache: dict[str, tuple[datetime, dict]] = {}
        self.calls = 0

    def lookup(self, di: str, timeout: float = 10.0) -> dict:
        di = str(di).strip()
        lookup_path(di)
        hit = self.cache.get(di)
        now = self.clock()
        if hit and now - hit[0] < self.ttl:
            return hit[1] | {"cached": True}
        self.calls += 1
        rec = parse_lookup(self.fetch(di, timeout), now)
        self.cache[di] = (now, rec)
        return rec


def http_fetch(di: str, timeout: float = 10.0, session: Any = None) -> dict:
    import requests
    s = session or requests
    try:
        r = s.get(BASE_URL + lookup_path(di), timeout=timeout)
    except requests.Timeout as e:
        raise TimeoutError(str(e)) from e
    except requests.ConnectionError as e:
        raise ServiceUnavailable(str(e)) from e
    if r.status_code == 429:
        raise RateLimited("AccessGUDID: 429")
    if r.status_code >= 500:
        raise ServiceUnavailable(f"AccessGUDID: {r.status_code}")
    if r.status_code == 404:
        raise DeviceNotFound(di)
    r.raise_for_status()
    return r.json()


def uc_fetch(sql, ai_schema: str) -> Callable[[str, float], dict]:
    """Through the governed function: SELECT {ai}.gudid_live_lookup(:di) on the app's warehouse (the caller needs
    EXECUTE on the function; the function's owner holds USE CONNECTION). Errors of the HTTP call come back as SQL
    errors; a 429 or 5xx in the message is mapped to the transient errors."""
    def fetch(di: str, timeout: float = 10.0) -> dict:
        try:
            rows = sql.query(f"SELECT {ai_schema}.gudid_live_lookup(:di) AS r", {"di": di})
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "429" in msg:
                raise RateLimited(msg) from e
            if any(code in msg for code in ("500", "502", "503", "504", "timed out")):
                raise ServiceUnavailable(msg) from e
            raise
        text = rows[0]["r"] if rows else None
        if not text:
            raise DeviceNotFound(di)
        return json.loads(text)
    return fetch


class DemoGudidApi:
    """A stand-in for AccessGUDID in the local demo and tests: answers from the synthetic catalog's devices, with
    a newer public version than the lakehouse copy. down=True: every call is a 503; delay_s: a slow API."""

    def __init__(self, catalog, version_date: str = "2026-11-14") -> None:
        self.catalog, self.version_date = catalog, version_date
        self.down, self.delay_s = False, 0.0

    def __call__(self, di: str, timeout: float = 10.0) -> dict:
        import time
        if self.delay_s:
            time.sleep(min(self.delay_s, timeout))
            if self.delay_s >= timeout:
                raise TimeoutError(f"AccessGUDID: no answer within {timeout:g} s")
        if self.down:
            raise ServiceUnavailable("AccessGUDID: 503 (demo outage)")
        from quote_ai.mock import catalog as mock
        from quote_ai.mock import range as mock_range
        for p in [x["product_id"] for x in mock.products() + mock_range.products()]:
            d = self.catalog.device(p)
            if d and d["primary_di"] == di:
                return {"gudid": {"device": {
                    "brandName": d["brand_name"], "companyName": d["company_name"],
                    "deviceDescription": d["device_description"], "publicVersionNumber": 3,
                    "publicVersionDate": self.version_date, "deviceRecordStatus": "Published",
                    "deviceCommDistributionStatus": "In Commercial Distribution",
                    "labeledContainsNRL": d["labeled_contains_nrl"], "labeledNoNRL": d["labeled_no_nrl"],
                    "identifiers": {"identifier": [{"deviceId": di, "deviceIdType": "Primary"}]}}}}
        raise DeviceNotFound(di)
