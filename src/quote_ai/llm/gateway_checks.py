"""The Model access suite (AI-06): one allowed call and three that the gateway must refuse.

outcome() reads what came back from a model service call (a reply, or the exception the client raised) and names it:
allowed, no_grant (HTTP 403), rate_limited (HTTP 429) or blocked (a service policy denied it: HTTP 200 with a block
message). Plain Python, so the suite's logic is unit-tested; the calls themselves run in
notebooks/quote_ai/06_gateway_tests.py, as different identities.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parents[3] / "evaluation" / "quote_ai" / "v1" / "model_access" / "cases.jsonl"
# Verify the exact block message of a service policy before recording; these markers are matched case-insensitively.
BLOCK_MARKERS = ("blocked by", "policy violation", "request was blocked", "denied by")


def _status(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "http_status"):
        v = getattr(exc, attr, None)
        if isinstance(v, int):
            return v
    resp = getattr(exc, "response", None)
    return getattr(resp, "status_code", None)


def outcome(result: Any) -> str:
    """allowed / no_grant / rate_limited / blocked / error, for a reply (quote_ai.llm.client.Reply) or an exception."""
    if isinstance(result, BaseException):
        code = _status(result)
        text = str(result).lower()
        if code == 403 or "permission" in text and "denied" in text:
            return "no_grant"
        if code == 429 or "rate limit" in text:
            return "rate_limited"
        return "error"
    text = (getattr(result, "text", None) or "").lower()
    return "blocked" if any(m in text for m in BLOCK_MARKERS) else "allowed"


def load_cases(path: Path = SUITE) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def verdict(case: dict, outcomes: list[str]) -> bool:
    """A case passes when its calls produced the expected outcome; for the limit case, the first `allowed_first`
    calls pass and every later one is refused."""
    if case["expected"] == "rate_limited":
        n = case.get("allowed_first", 0)
        return all(o == "allowed" for o in outcomes[:n]) and bool(outcomes[n:]) and \
            all(o == "rate_limited" for o in outcomes[n:])
    return bool(outcomes) and all(o == case["expected"] for o in outcomes)
