"""An approval is bound to one quote version: its requirements, lines, prices and evidence snapshot (plan 4.2)."""
from __future__ import annotations

import hashlib
import json

from quote_ai.domain.models import Quote


def terms_hash(q: Quote) -> str:
    terms = {
        "requirements_version": q.requirements.version,
        "delivery_days": q.requirements.delivery_days,
        "lines": [(l.size, l.sku_id, l.quantity, l.unit, l.unit_price) for l in q.lines],
        "discount": q.discount,
        "evidence_snapshot": q.evidence_snapshot,
        "inventory_snapshot": q.inventory_snapshot,
    }
    return hashlib.sha256(json.dumps(terms, sort_keys=True).encode()).hexdigest()
