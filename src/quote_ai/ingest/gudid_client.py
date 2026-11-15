"""AccessGUDID API v3, in plain Python: the device history of one record, one row per published version.

GET <base>/devices/history.json?di=... (or record_key=...) returns {"history": {<version>: {...}, ...}}; each version
carries primary_di, publicDeviceRecordKey, publicVersionNumber, publicVersionStatus, publicVersionDate, creationDate
and the full record as XML. The history isn't paged: one call per record. The PySpark data source (api_source.py)
runs this client on the workers.
"""
from __future__ import annotations

import json
from typing import Any, Iterator

from quote_ai.ingest.http import Http
from quote_ai.ingest.lineage import snake_case

# The columns of gudid_bronze.api_device_history before lineage; payload keeps the version exactly as returned.
HISTORY_SCHEMA = ("primary_di string, public_device_record_key string, public_version_number string, "
                  "public_version_status string, public_version_date string, creation_date string, "
                  "request string, payload string")
HISTORY_FIELDS = [c.split()[0] for c in HISTORY_SCHEMA.split(", ")]


def _versions(body: Any) -> list[dict]:
    """The history as a list of versions, oldest first: tolerant of an object keyed by version, or a list."""
    history = (body or {}).get("history") or {}
    if isinstance(history, dict):
        def order(k: str):
            return (0, int(k)) if str(k).isdigit() else (1, str(k))
        return [history[k] for k in sorted(history, key=order) if isinstance(history[k], dict)]
    return [v for v in history if isinstance(v, dict)]


class GudidClient:
    def __init__(self, base_url: str, endpoint: str, http: Http) -> None:
        self.url = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
        self.http = http

    def history(self, param: str, value: str) -> Iterator[tuple]:
        """One tuple per published version (HISTORY_FIELDS order). A record unknown to GUDID yields nothing."""
        resp = self.http.get(self.url, {param: value})
        if resp is None:
            return
        request = f"{self.url}?{param}={value}"
        for v in _versions(resp.json()):
            row = {snake_case(k): v.get(k) for k in v}
            yield (
                str(row.get("primary_di") or (value if param == "di" else "")) or None,
                str(row.get("public_device_record_key") or (value if param == "record_key" else "")) or None,
                _s(row.get("public_version_number")),
                _s(row.get("public_version_status")),
                _s(row.get("public_version_date")),
                _s(row.get("creation_date")),
                request,
                json.dumps(v, sort_keys=True),
            )


def _s(v: Any) -> str | None:
    return None if v is None else str(v)
