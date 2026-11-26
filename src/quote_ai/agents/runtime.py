"""What the agent runs with, wherever it runs: the tools' dependencies, the model client and the endpoint.

  demo_deps()     the synthetic stores (Gloves A to E, Lakeside), the stand-in win model and Genie, a stand-in for
                  AccessGUDID: tests, CI suites, the local demo (QUOTE_AI_DEMO=1)
  demo_quote()    the Lakeside quote, confirmed (r2, four lines), in an in-memory QuoteService
  deps_from_env() in the workspace: the lakehouse through a SQL warehouse (the rules as UC functions), the AI Search
                  indexes, the Genie Agent, the win model endpoint, the live GUDID lookup through the UC connection
  model_client()  the stand-in (QUOTE_AI_AGENT_MODEL=standin) or this workspace's OpenAI-compatible client
                  through Unity Gateway, and the chat model service name

Environment (the agent app's app.yaml, AI-20): DATABRICKS_WAREHOUSE_ID, QUOTE_AI_QUOTES_VOLUME (gives the catalog
and schema names) or QUOTE_AI_CATALOG and QUOTE_AI_SCHEMAS (logical=physical, as the jobs pass them),
QUOTE_AI_CHAT_SERVICE, QUOTE_AI_GENIE_SPACE_ID, QUOTE_AI_WIN_ENDPOINT, QUOTE_AI_SEARCH_ENDPOINT.
"""
from __future__ import annotations

import os
from typing import Any

from quote_ai.agents.tools import ToolDeps


def demo_genie(question: str) -> dict:
    """The sales analyst's answer for Lakeside's last year, from the synthetic order lines (demo data)."""
    from quote_ai.mock import sales
    lines = [l for l in sales.order_lines() if l["account_id"] == "ACC-001"]
    last = max(l["order_date"] for l in lines)
    year = [l for l in lines if (last - l["order_date"]).days <= 365]
    boxes = sum(l["quantity"] for l in year)
    avg = round(sum(l["quantity"] * l["unit_price"] for l in year) / boxes, 2) if boxes else None
    sql = ("SELECT sum(quantity) AS boxes, round(sum(quantity * unit_price) / sum(quantity), 2) AS avg_price "
           "FROM quote_ai_sales.order_metrics WHERE account_id = 'ACC-001' AND order_date > add_months(current_date(), -12)")
    return {"text": f"Lakeside Regional Clinic bought {boxes} boxes in the last year, at ${avg:.2f} a box on average "
                    f"(demo data).", "sql": sql, "columns": ["boxes", "avg_price"], "rows": [[boxes, avg]]}


def demo_deps(live: bool = True, genie: Any = demo_genie, live_first: bool = False) -> ToolDeps:
    from quote_ai.agents import standin
    from quote_ai.agents.skills import SkillLibrary
    from quote_ai.gudid.api_client import DemoGudidApi, LiveGudid
    from quote_ai.mock import knowledge
    from quote_ai.search import docs, local
    from quote_ai.services.shortlist import LocalRules
    from quote_ai.storage.accounts import DemoAccounts
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    from quote_ai.storage.product_catalog import DemoProductCatalog
    store, cat = DemoEvidenceStore(), DemoProductCatalog()
    return ToolDeps(catalog=cat, evidence=store, rules=LocalRules(store, cat),
                    sku_index=local.InMemoryIndex(docs.demo_docs(store.rows)), win_model=standin.demo_win_model,
                    genie=genie,
                    knowledge_index=local.InMemoryIndex(knowledge.demo_chunks(store.rows), text_column="text",
                                                        primary_key="chunk_id", keyword_columns=()),
                    accounts=DemoAccounts(), live_gudid=LiveGudid(DemoGudidApi(cat)) if live else None,
                    skills=SkillLibrary(), live_first=live_first)


def demo_quote(service=None, rep: str = "rep.alice", quote_id: str = "Q-LAKESIDE"):
    """The Lakeside quote, requirements confirmed (r2: S 60, M 140, L 140, XL 60 boxes of 100)."""
    from quote_ai.domain.models import Requirements
    from quote_ai.services.quote_service import QuoteService
    from quote_ai.storage.repository import InMemoryQuoteRepository
    svc = service or QuoteService(InMemoryQuoteRepository())
    r1 = Requirements(version=1, material="nitrile", powder_free=True, intended_use="exam", latex_free=True,
                      chemo_tested=True, sizes=["S", "M", "L", "XL"], quantity=400, unit="box",
                      target_unit_price=8.00, delivery_days=10, missing=["quantity_per_size", "gloves_per_unit"])
    text = ("Lakeside Regional Clinic needs 400 boxes of powder-free nitrile exam gloves, chemo-tested, latex-free, "
            "sizes S to XL, non-sterile is fine. They pay about $8 a box today and need delivery in 10 days.")
    q = svc.create("Lakeside Regional Clinic", rep, text, r1, quote_id=quote_id)
    return svc.confirm(q.quote_id, q.revision, rep, {"S": 60, "M": 140, "L": 140, "XL": 60}, "box", 100)


def _schemas() -> dict[str, str]:
    """From the agent app's quotes-volume resource (catalog.schema.volume in quote_ai_sales, as the QuoteAI app does),
    or from QUOTE_AI_CATALOG and QUOTE_AI_SCHEMAS (a job or a notebook)."""
    from quote_ai.storage import sql_files
    if os.environ.get("QUOTE_AI_QUOTES_VOLUME"):
        catalog, sales, _ = os.environ["QUOTE_AI_QUOTES_VOLUME"].split(".")
        return sql_files.names_from_anchor(catalog, "quote_ai_sales", sales)
    return sql_files.names_for(os.environ["QUOTE_AI_CATALOG"],
                               dict(p.split("=", 1) for p in os.environ.get("QUOTE_AI_SCHEMAS", "").split(",") if p))


def deps_from_env() -> ToolDeps:
    if os.environ.get("QUOTE_AI_DEMO") == "1":
        return demo_deps()
    from quote_ai.agents.skills import SkillLibrary
    from quote_ai.gudid.api_client import LiveGudid, uc_fetch
    from quote_ai.search import index
    from quote_ai.services.shortlist import SqlRules
    from quote_ai.storage.accounts import SqlAccounts
    from quote_ai.storage.delta_repository import DatabricksSql
    from quote_ai.storage.evidence_store import SqlEvidenceStore
    from quote_ai.storage.product_catalog import SqlProductCatalog
    names = _schemas()
    sql = DatabricksSql(os.environ["DATABRICKS_WAREHOUSE_ID"])
    evidence = SqlEvidenceStore(sql, names)
    endpoint = os.environ.get("QUOTE_AI_SEARCH_ENDPOINT", index.ENDPOINT)
    genie = win = None
    if os.environ.get("QUOTE_AI_GENIE_SPACE_ID"):
        from quote_ai.semantic.genie import client
        space = os.environ["QUOTE_AI_GENIE_SPACE_ID"]
        genie = lambda question: client.ask(space, question)  # noqa: E731
    if os.environ.get("QUOTE_AI_WIN_ENDPOINT"):
        from quote_ai.ml import score
        win = score.query_endpoint(os.environ["QUOTE_AI_WIN_ENDPOINT"])
    return ToolDeps(catalog=SqlProductCatalog(sql, names), evidence=evidence, rules=SqlRules(sql, names, evidence),
                    sku_index=_LazyIndex(names["ai"], "sku", endpoint), win_model=win, genie=genie,
                    knowledge_index=_LazyIndex(names["ai"], "knowledge", endpoint), accounts=SqlAccounts(sql, names),
                    live_gudid=LiveGudid(uc_fetch(sql, names["ai"])), skills=SkillLibrary(), live_first=True)


class _LazyIndex:
    def __init__(self, ai_schema: str, what: str, endpoint: str) -> None:
        self.args, self._index = (ai_schema, what, endpoint), None

    def similarity_search(self, **kwargs):
        if self._index is None:
            from quote_ai.search import index
            self._index = index.get_index(*self.args)
        return self._index.similarity_search(**kwargs)


def model_client() -> tuple[Any, str]:
    """(client, endpoint). Verify before recording: tool calling on the chosen Foundation Model behind
    quote_ai_chat in the region, through the OpenAI-compatible client."""
    if os.environ.get("QUOTE_AI_AGENT_MODEL", "standin" if os.environ.get("QUOTE_AI_DEMO") == "1" else "") == "standin":
        from quote_ai.agents.standin import StandInClient
        return StandInClient(), "standin"
    from quote_ai.llm import endpoints
    from quote_ai.llm.client import workspace_client
    service = os.environ.get("QUOTE_AI_CHAT_SERVICE")          # catalog.schema.name, from the app's resource
    return workspace_client(), endpoints.model(service.split(".")[-1] if service else endpoints.CHAT)
