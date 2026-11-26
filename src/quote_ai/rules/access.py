"""Who may see what (AI-24): the data rules Unity Catalog enforces, written once more in Python for the places UC can't
reach (the local demo, the data authorization suite on CI) and kept identical to the SQL by a test.

  rules/sql/row_filters.sql    account_filter(account_id): a manager sees every row; anyone else only the rows of an
                               account they own (quote_ai_sales.rep_account). On quote_fact and order_line (what Genie
                               and the metric views read), and rep_filter on rep_account itself
  rules/sql/column_masks.sql   mask_cost(v): unit_cost is NULL for anyone but a manager (quote_fact, order_line); the
                               price book view masks its cost column the same way and shows floor_price instead
                               (product/sku_model.sql), so a rep can be priced without seeing cost

In the workspace these run inside Unity Catalog, because the back end queries as the signed-in rep (user
authorization, app/backend/deps.py). An identity is a user (with their groups) or the app's service principal; the
app's identity is never used for a rep's data: that was the gap (Genie as the app saw every account and the cost).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

MANAGERS_GROUP = "DBxLab_Sales_Managers"
REPS_GROUP = "DBxLab_Sales_Reps"
COST_COLUMNS = ("unit_cost", "cost", "avg_cost", "margin_pct")
NO_ACCESS = "no access to this account"


@dataclass(frozen=True)
class Identity:
    user: str                                    # the signed-in rep's email, or the app's application ID
    groups: frozenset[str] = field(default_factory=frozenset)
    token: str | None = field(default=None, repr=False, compare=False)   # the forwarded user token (never logged)
    kind: str = "user"                           # user | app

    @property
    def is_manager(self) -> bool:
        return MANAGERS_GROUP in self.groups

    @property
    def rep_id(self) -> str:
        return self.user.split("@", 1)[0].strip().lower()


def owned_accounts(identity: Identity, owners: Mapping[str, Iterable[str]]) -> set[str] | None:
    """The account IDs the identity's rows are filtered to; None = every account (a manager, or the app's service
    principal, which is exactly why a rep's question must never run as the app)."""
    if identity.kind == "app" or identity.is_manager:
        return None
    return set(owners.get(identity.rep_id, ()))


def can_see_account(identity: Identity, account_id: str, owners: Mapping[str, Iterable[str]]) -> bool:
    allowed = owned_accounts(identity, owners)
    return allowed is None or account_id in allowed


def can_see_cost(identity: Identity) -> bool:
    """mask_cost: managers see cost and margin. The app's own identity sees them too in UC (it has the grant until
    AI-24 revokes it); a rep's data never goes through it."""
    return identity.kind == "app" or identity.is_manager


def filter_rows(rows: Iterable[Mapping], identity: Identity, owners: Mapping[str, Iterable[str]],
                account_key: str = "account_id") -> list[dict]:
    """account_filter, row by row."""
    allowed = owned_accounts(identity, owners)
    return [dict(r) for r in rows if allowed is None or r.get(account_key) in allowed]


def mask_row(row: Mapping, identity: Identity, columns: Iterable[str] = COST_COLUMNS) -> dict:
    """mask_cost, column by column: NULL for anyone who may not see cost."""
    out = dict(row)
    if not can_see_cost(identity):
        for c in columns:
            if c in out:
                out[c] = None
    return out
