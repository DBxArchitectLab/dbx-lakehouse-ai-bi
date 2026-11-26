"""A rep's data, read as the rep (AI-24): the accounts they may see, the price book (cost masked) and Ask the data.

  SqlUserData    the workspace: every query with the signed-in rep's forwarded token (DatabricksSql(token=...)), and
                 Genie with a WorkspaceClient on that token, so Unity Catalog's row filter and column mask apply on
                 every path, Genie included. The app's service principal is not involved
  DemoUserData   local demo and CI: the same rules (quote_ai.rules.access) over the synthetic data

Replaces the filter by rep the back end applied in code since AI-07 (routers/quotes.py _own): whether a rep may open a
quote is now whether the account is among the accounts Unity Catalog lets them read.
"""
from __future__ import annotations

import math
from typing import Any

from quote_ai.rules import access
from quote_ai.rules.pricing import PriceBookRow


def _customers() -> dict[str, str]:
    from quote_ai.ingest.synthetic import CUSTOMERS          # AI-02's synthetic accounts (read, never changed)
    return {a: n for a, n, _, _ in CUSTOMERS}


def _owners() -> dict[str, list[str]]:
    from quote_ai.mock import knowledge
    return knowledge.REP_ACCOUNTS


class DemoUserData:
    def __init__(self, identity: access.Identity) -> None:
        self.identity = identity

    @property
    def asked_as(self) -> str:
        return "the app's service principal" if self.identity.kind == "app" else f"you ({self.identity.user})"

    def accounts(self) -> dict[str, str]:
        """account_id -> name of every account the identity may read."""
        return {a: n for a, n in _customers().items() if access.can_see_account(self.identity, a, _owners())}

    def price_book(self) -> list[PriceBookRow]:
        from quote_ai.storage.product_catalog import DemoProductCatalog
        out = []
        for r in DemoProductCatalog().price_book():
            floor = math.ceil(round(r.cost * (1 + r.min_margin) * 100, 6)) / 100     # as the view: up to the cent
            out.append(r.model_copy(update={"floor_price": floor,
                                            "cost": r.cost if access.can_see_cost(self.identity) else None}))
        return out

    def ask_data(self, question: str) -> dict:
        """The sales analyst over the synthetic order lines, filtered and masked for this identity: what each account
        pays per box, and (managers only) the cost and margin."""
        from quote_ai.mock import sales
        rows = access.filter_rows(sales.order_lines(), self.identity, _owners())
        names = _customers()
        asked = [a for a, n in names.items() if n.split()[0].lower() in question.lower()]
        out = []
        for a in sorted({r["account_id"] for r in rows if not asked or r["account_id"] in asked}):
            mine = [r for r in rows if r["account_id"] == a]
            boxes = sum(r["quantity"] for r in mine)
            price = round(sum(r["quantity"] * r["unit_price"] for r in mine) / boxes, 2)
            cost = round(sum(r["quantity"] * r["unit_cost"] for r in mine) / boxes, 2)
            out.append(access.mask_row({"account": names[a], "avg_price": price, "avg_cost": cost,
                                        "margin_pct": round(100 * (1 - cost / price), 1)}, self.identity))
        sql = ("SELECT account, MEASURE(avg_unit_price) AS avg_price, MEASURE(avg_unit_cost) AS avg_cost, "
               "MEASURE(margin_pct) AS margin_pct FROM quote_ai_sales.order_metrics GROUP BY account")
        cols = ["account", "avg_price", "avg_cost", "margin_pct"]
        text = "; ".join(f"{r['account']} pays ${r['avg_price']:.2f} a box on average" for r in out) or \
            "No rows: you have no access to that account."
        return {"text": text + " (demo data).", "sql": sql, "columns": cols,
                "rows": [[r[c] for c in cols] for r in out]}


class SqlUserData:
    """As the rep, in the workspace. Verify before recording: the x-forwarded-access-token header and its scopes
    (sql, dashboards.genie in quote_ai_app.app.yml), and WorkspaceClient(host=, token=, auth_type="pat") with it."""

    def __init__(self, identity: access.Identity, warehouse_id: str, names: dict, space_id: str | None) -> None:
        if not identity.token:
            raise PermissionError("no user token forwarded: the app must not fall back to its own identity")
        from quote_ai.storage.delta_repository import DatabricksSql
        self.identity, self.names, self.space_id = identity, names, space_id
        self.sql = DatabricksSql(warehouse_id, token=identity.token)

    @property
    def asked_as(self) -> str:
        return f"you ({self.identity.user})"

    def accounts(self) -> dict[str, str]:
        rows = self.sql.query(f"SELECT DISTINCT c.account_id, c.name FROM {self.names['sales']}.rep_account r "
                              f"JOIN {self.names['sales']}.customer c ON c.account_id = r.account_id")
        return {r["account_id"]: r["name"] for r in rows}            # rep_account's row filter decides

    def price_book(self) -> list[PriceBookRow]:
        from quote_ai.storage.product_catalog import SqlProductCatalog
        return SqlProductCatalog(self.sql, self.names).price_book()  # the view masks cost for reps

    def ask_data(self, question: str) -> dict:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.core import Config

        from quote_ai.semantic.genie import client
        if not self.space_id:
            raise LookupError("no Genie Agent configured (QUOTE_AI_GENIE_SPACE_ID)")
        w = WorkspaceClient(host=Config().host, token=self.identity.token, auth_type="pat")
        return client.ask(self.space_id, question, client=w)


def demo(identity: access.Identity) -> Any:
    return DemoUserData(identity)
