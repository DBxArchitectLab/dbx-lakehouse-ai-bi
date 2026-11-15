"""HTTP with the manners a public service deserves: a request rate limit, retries with backoff on 429 and 5xx
(honouring Retry-After), and an error that names what failed. Shared by the downloader and the API source.

The clock and sleep are injected, so the tests run instantly and assert exactly how long we would have waited.
"""
from __future__ import annotations

import time
from typing import Any, Callable

import requests

RETRYABLE = {429, 500, 502, 503, 504}
MAX_WAIT_S = 120.0


class HttpError(RuntimeError):
    """Gave up: after the last retry, or on a status that retrying won't fix."""


class RateLimiter:
    """At most max_rps requests per second: waits for the gap since the last request."""

    def __init__(self, max_rps: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval = 1.0 / max_rps
        self.clock, self.sleep = clock, sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.interval:
            self.sleep(self.interval - (now - self._last))
            now = self.clock()
        self._last = now


def retry_after(resp: Any) -> float | None:
    value = (getattr(resp, "headers", None) or {}).get("Retry-After")
    try:
        return min(float(value), MAX_WAIT_S) if value is not None else None
    except ValueError:      # an HTTP date: fall back to our own backoff
        return None


class Http:
    def __init__(self, *, max_rps: float = 2, retries: int = 5, backoff_s: float = 2, timeout_s: float = 30,
                 session: Any = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep, user_agent: str = "quote-ai-ingest/0.1") -> None:
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self.limiter = RateLimiter(max_rps, clock, sleep)
        self.retries, self.backoff_s, self.timeout_s, self.sleep = retries, backoff_s, timeout_s, sleep
        self.requests_made = 0
        self.retried = 0

    def get(self, url: str, params: dict | None = None, *, stream: bool = False, allow_404: bool = True) -> Any:
        """The response, or None for a 404 (when allowed: no release that day, no such device)."""
        last = None
        for attempt in range(self.retries + 1):
            self.limiter.wait()
            self.requests_made += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout_s, stream=stream)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = type(e).__name__
            else:
                if resp.status_code == 404 and allow_404:
                    return None
                if resp.status_code not in RETRYABLE:
                    if resp.status_code >= 400:
                        raise HttpError(f"GET {url} {params or ''}: HTTP {resp.status_code}")
                    return resp
                last = f"HTTP {resp.status_code}"
                wait = retry_after(resp)
                if wait is not None and attempt < self.retries:
                    self.retried += 1
                    self.sleep(wait)
                    continue
            if attempt < self.retries:
                self.retried += 1
                self.sleep(min(self.backoff_s * 2 ** attempt, MAX_WAIT_S))
        raise HttpError(f"GET {url} {params or ''}: gave up after {self.retries + 1} tries (last: {last})")
