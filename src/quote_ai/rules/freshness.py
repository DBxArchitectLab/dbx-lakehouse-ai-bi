"""Freshness before issue (plan 4.2, AI-20): a fallback or stale copy may inform the rep; it never issues a quote.
The limits live in config/quote_ai/policy.yml (GUDID-derived evidence 48 h, stock 24 h); QuoteService.issue is the
one place they are checked, and the approval flow (AI-21) calls it."""
from __future__ import annotations

from datetime import datetime

from quote_ai.rules.policy import FreshnessPolicy


def stale_inputs(as_of: dict[str, datetime], now: datetime, policy: FreshnessPolicy) -> list[str]:
    """Names of the inputs older than the policy allows; an input with no age limit is never stale."""
    stale = []
    for name, ts in sorted(as_of.items()):
        limit = policy.max_age_hours.get(name)
        if limit is not None and (now - ts).total_seconds() > limit * 3600:
            stale.append(name)
    return stale


def stale_details(as_of: dict[str, datetime], now: datetime, policy: FreshnessPolicy) -> list[str]:
    """AI-20: each stale input with its date, for the refusal: "gudid as of 2026-11-12 09:00 UTC"."""
    return [f"{name} as of {as_of[name].strftime('%Y-%m-%d %H:%M UTC')}"
            for name in stale_inputs(as_of, now, policy)]
