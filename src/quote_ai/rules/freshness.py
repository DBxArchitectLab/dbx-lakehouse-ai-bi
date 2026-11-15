"""Freshness before issue (plan 4.2): a fallback or stale copy may inform the rep; it never issues a quote."""
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
