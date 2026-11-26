"""An approval is bound to one quote version: its requirements, lines, prices, discount and evidence snapshot
(plan 4.2, AI-21). Rules only: no writes in this file. Writing the approval is QuoteService's job, in one
transaction with the state change and the quote_event.

  terms_hash     the terms of one quote version, hashed: the quote, the version, the confirmed requirements version
                 (r2, r3, ...) and delivery date, the evidence and inventory snapshots, each line's size, SKU, boxes and
                 unit price, and the discount
  check_approver no self-approval; only members of the approvers' group (DBxLab_Sales_Managers), checked from the
                 signed-in identity, never from a field in the request
  can_issue      True only if the latest approval is for this version and this hash. Any edit makes a new version
                 (QuoteService), so an old approval no longer matches
"""
from __future__ import annotations

import hashlib
import json

from quote_ai.domain.models import Approval, Quote

APPROVERS = "DBxLab_Sales_Managers"
MANAGER_ROLE = "sales_manager"


class PolicyError(PermissionError):
    """The approval policy refused (HTTP 403)."""


def terms_hash(q: Quote) -> str:
    terms = {
        "quote_id": q.quote_id,
        "version": q.version,
        "requirements_version": q.requirements.version,
        "delivery_days": q.requirements.delivery_days,
        "lines": [(l.size, l.sku_id, l.quantity, l.unit, l.unit_price) for l in sorted(q.lines, key=lambda l: l.size)],
        "discount": q.discount,
        "evidence_snapshot": q.evidence_snapshot,
        "inventory_snapshot": q.inventory_snapshot,
    }
    return hashlib.sha256(json.dumps(terms, sort_keys=True).encode()).hexdigest()


def is_approver(groups: set[str]) -> bool:
    return APPROVERS in groups or MANAGER_ROLE in groups


def check_approver(q: Quote, approver: str, groups: set[str]) -> None:
    if approver == q.rep:
        raise PolicyError("no self-approval: the approver must not be the quote's rep")
    if not is_approver(groups):
        raise PolicyError(f"{approver} is not a sales manager ({APPROVERS})")


def can_issue(q: Quote, latest: Approval | None) -> bool:
    return latest is not None and latest.quote_version == q.version and latest.terms_hash == terms_hash(q)
