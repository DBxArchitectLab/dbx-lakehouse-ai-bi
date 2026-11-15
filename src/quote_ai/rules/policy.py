"""The policy the rules read: config/quote_ai/policy.yml, so a change is reviewed like code."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel


class PricingPolicy(BaseModel):
    rep_discount_limit: float = 0.05
    manager_discount_limit: float = 0.15


class FreshnessPolicy(BaseModel):
    max_age_hours: dict[str, int] = {"gudid": 48, "evidence": 48, "inventory": 24, "price_book": 168}


class Policy(BaseModel):
    pricing: PricingPolicy = PricingPolicy()
    freshness: FreshnessPolicy = FreshnessPolicy()


def load_policy(path: str | Path) -> Policy:
    return Policy.model_validate(yaml.safe_load(Path(path).read_text()))
