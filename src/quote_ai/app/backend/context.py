"""What the routes work with: QuoteService (on Delta), the product catalog and the extraction. Built once, from the
app's environment (app.yaml: each value comes from an app resource, never a literal); tests pass their own.

  DATABRICKS_WAREHOUSE_ID   the SQL warehouse (resource sql-warehouse)
  QUOTE_AI_QUOTES_VOLUME    catalog.schema.volume in quote_ai_sales (resource quotes-volume): gives the catalog and the
                            deployed schema names (a personal dev copy prefixes them all alike)
  QUOTE_AI_EXTRACT_SERVICE  catalog.schema.name of the quote_ai_extract model service (resource extract-service)
  QUOTE_AI_PROMPT_ALIAS     the prompt alias every prompt is read by (AI-23: champion; a missing alias stops the app)
  QUOTE_AI_DOCUMENTS_VOLUME catalog.schema.volume of quote_ai_documents (resource documents-volume, AI-09)
  QUOTE_AI_GENIE_SPACE_ID   the Genie Agent the Ask the data panel asks (resource genie-space, AI-11)
  QUOTE_AI_DASHBOARD_ID     the sales overview dashboard My pipeline embeds (AI-10; optional, else found by name)
  QUOTE_AI_WIN_ENDPOINT     the win model's serving endpoint the Quote page asks (resource win-model, AI-14)
  QUOTE_AI_CHAT_SERVICE     catalog.schema.name of quote_ai_chat (resource chat-service, AI-16): the RAG generator
  QUOTE_AI_KA_ENDPOINT      optional: the Knowledge Assistant's endpoint (AI-16, the "buy" option)
  QUOTE_AI_SEARCH_ENDPOINT  optional: the AI Search endpoint of the indexes (AI-15; default quote_ai.search.index.ENDPOINT);
                            the index names come from the AI schema (<ai schema>.sku_search_index)
  QUOTE_AI_DEMO=1           local run with no workspace data: in-memory quotes and the synthetic catalog
Season 5:
  the agent (AI-18)         QuoteAgent in-process, on the same stores as the routes (agents.tools.ToolDeps); the
                            stand-in model with QUOTE_AI_AGENT_MODEL=standin (the demo's default)
  QUOTE_AI_AGENT_URL        AI-20: the agent on the Agent Runtime (its app URL); else derived from DATABRICKS_APP_NAME
                            (quote-ai-dev -> quote-ai-agent-dev); QUOTE_AI_AGENT_LOCAL=1 keeps it in-process
  QUOTE_AI_STORE=lakebase   AI-20: quotes and memory on Lakebase (PGHOST, PGDATABASE, PGUSER from the database
                            resource); otherwise Delta (AI-07) and memory on a local SQLite file
  QUOTE_AI_MANAGERS_GROUP   AI-21: the approvers' group (DBxLab_Sales_Managers); QUOTE_AI_MANAGERS: a comma list of
                            manager emails for a local run
Season 6:
  feedback (AI-22)          thumbs up or down on the answer's MLflow trace; a thumbs down -> quote_ai_eval.candidate_cases
  as_user (AI-24)           the rep's accounts, price book and Ask the data, queried with the rep's forwarded token
                            (row filters and masks apply); the app's service principal keeps audit and eval only
Season 7:
  ops (AI-26)               the Ops page: quote_ai_monitoring through the warehouse (SqlMonitoring); in the local demo,
                            a week of synthetic traffic through the same SQL on SQLite (LocalMonitoring)
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from quote_ai.services.quote_service import QuoteService
from quote_ai.storage.evidence_store import DemoDocumentStore, DemoEvidenceStore, DocumentStore, EvidenceStore
from quote_ai.storage.product_catalog import DemoProductCatalog, ProductCatalog
from quote_ai.storage.repository import InMemoryQuoteRepository


@dataclass
class Context:
    service: QuoteService
    catalog: ProductCatalog
    extractor: Callable[[str], Any]      # request text -> quote_ai.llm Requirements
    evidence: EvidenceStore | None = None            # AI-08, AI-09: evidence rows and supplier documents
    documents: DocumentStore | None = None           # AI-09: request PDFs into the volume, parsed
    dashboard: Callable[[], dict] | None = None      # AI-10: {"url": embed URL or None, "reason": ...}
    genie: Callable[[str], dict] | None = None       # AI-11: question -> {"text", "sql", "columns", "rows"}
    win_model: Callable[[list[dict]], list] | None = None   # AI-14: request records -> predictions, one call
    sku_index: Any = None             # AI-15: the SKU search index (AI Search, or quote_ai.search.local in a demo)
    rules: Any = None                 # AI-15: quote_ai.services.shortlist LocalRules or SqlRules
    knowledge_index: Any = None       # AI-16: the knowledge chunks index
    generate: Callable[[str], str] | None = None     # AI-16: prompt -> the model's JSON (None: the extractive stand-in)
    ka: Callable[[str], Any] | None = None           # AI-16: question -> the Knowledge Assistant's response
    accounts: Callable[[str], list[str]] | None = None   # AI-17: signed-in user -> the accounts they may read
    compare_template: str | None = None              # AI-16: compare_explain from the Prompt Registry
    compare_prompt_version: str = "compare_explain/v1-local"
    cache: dict = field(default_factory=dict)        # AI-16: answers per quote version, question, scope, accounts
    agent: Any = None             # AI-18: .events(messages, quote, rep, customer_facts) -> the agent's events
    memory: Any = None            # AI-20: quote_ai.agents.memory.Memory (conversations, confirmed customer facts)
    inputs_as_of: Callable[[Any], dict] | None = None   # AI-20: quote -> {input: as-of datetime} for issue()
    groups: Callable[[str], set[str]] | None = None     # AI-21: user -> the groups they belong to
    price_tier: Callable[[str], str] | None = None      # AI-18: account -> price book tier (default standard)
    feedback: Any = None          # AI-22: .rate(trace_id, rep, thumbs_up, comment, quote_id, surface)
    # AI-24: Identity -> the rep's data read as the rep (storage/user_data.py). A Context built by hand (tests, the
    # local demo) gets the demo rules; build_context gives the workspace SqlUserData (season5 below).
    as_user: Callable[[Any], Any] | None = field(default_factory=lambda: _demo_user_data)
    ops: Any = None               # AI-26: .view(quote_id) -> the Ops page (quote_ai.ops.monitoring)


def _demo_user_data(identity):
    from quote_ai.storage import user_data
    return user_data.demo(identity)


def _dashboard() -> Callable[[], dict]:
    def get() -> dict:
        from quote_ai.semantic import dashboard
        return dashboard.embed_info(os.environ.get("QUOTE_AI_DASHBOARD_ID"))
    return get


def _genie(space_id: str | None) -> Callable[[str], dict] | None:
    if not space_id:
        return None
    from quote_ai.semantic.genie import client
    return lambda question: client.ask(space_id, question)


def _win_model(endpoint: str | None) -> Callable[[list[dict]], list] | None:
    if not endpoint:
        return None
    from quote_ai.ml import score
    return score.query_endpoint(endpoint)


def _extractor(service_full_name: str | None, alias: str) -> Callable[[str], Any]:
    from quote_ai.llm import endpoints, extract
    if service_full_name:
        catalog, schema, name = service_full_name.split(".")
        uri = extract.prompt_uri(catalog, schema, alias)
        endpoint = endpoints.model(name)
    else:
        uri, endpoint = os.environ.get("QUOTE_AI_PROMPT_URI"), None
    return lambda text: extract.extract_requirements(text, endpoint=endpoint, uri=uri)


def build_context() -> Context:
    alias = os.environ.get("QUOTE_AI_PROMPT_ALIAS", "champion")
    extractor = _extractor(os.environ.get("QUOTE_AI_EXTRACT_SERVICE"), alias)
    genie = _genie(os.environ.get("QUOTE_AI_GENIE_SPACE_ID"))
    win_model = _win_model(os.environ.get("QUOTE_AI_WIN_ENDPOINT"))
    if os.environ.get("QUOTE_AI_DEMO") == "1":
        from quote_ai.search import docs, local
        from quote_ai.services.shortlist import LocalRules
        from quote_ai.mock import knowledge
        from quote_ai.storage.accounts import DemoAccounts
        catalog, evidence = DemoProductCatalog(), DemoEvidenceStore()
        return season5(Context(QuoteService(InMemoryQuoteRepository()), catalog, extractor, evidence,
                       DemoDocumentStore(), _dashboard(), genie, win_model,
                       sku_index=local.InMemoryIndex(docs.demo_docs(evidence.rows)),
                       rules=LocalRules(evidence, catalog),
                       knowledge_index=local.InMemoryIndex(knowledge.demo_chunks(evidence.rows), text_column="text",
                                                           primary_key="chunk_id", keyword_columns=()),
                       accounts=DemoAccounts()))
    from quote_ai.storage import sql_files
    from quote_ai.storage.delta_repository import DatabricksSql, DeltaQuoteRepository
    from quote_ai.storage.evidence_store import SqlDocumentStore, SqlEvidenceStore
    from quote_ai.storage.product_catalog import SqlProductCatalog
    catalog, sales_schema, _ = os.environ["QUOTE_AI_QUOTES_VOLUME"].split(".")
    names = sql_files.names_from_anchor(catalog, "quote_ai_sales", sales_schema)
    sql = DatabricksSql(os.environ["DATABRICKS_WAREHOUSE_ID"])
    volume = os.environ.get("QUOTE_AI_DOCUMENTS_VOLUME")
    from quote_ai.services.shortlist import SqlRules
    evidence = SqlEvidenceStore(sql, names)
    from quote_ai.llm import rag
    from quote_ai.storage.accounts import SqlAccounts
    from quote_ai.llm import prompts
    template, version = prompts.load_prompt(rag.PROMPT_NAME, alias, catalog, names["ai"].split(".", 1)[1])   # AI-23
    return season5(Context(QuoteService(quote_repository(sql, names)), SqlProductCatalog(sql, names), extractor,
                   evidence, SqlDocumentStore(sql, volume) if volume else None, _dashboard(),
                   genie, win_model, sku_index=_Lazy(lambda: _index(names["ai"], "sku")),
                   rules=SqlRules(sql, names, evidence),
                   knowledge_index=_Lazy(lambda: _index(names["ai"], "knowledge")),
                   generate=_generate(os.environ.get("QUOTE_AI_CHAT_SERVICE")), ka=_ka(os.environ.get("QUOTE_AI_KA_ENDPOINT")),
                   accounts=SqlAccounts(sql, names), compare_template=template, compare_prompt_version=version),
                   sql=sql, names=names)


def quote_repository(sql, names: dict):
    """Where quotes live: Delta (AI-07) until the AI-20 migration, Lakebase after it (QUOTE_AI_STORE=lakebase).
    QUOTE_AI_READ_ONLY=1 pauses writes while the migration copies and proves the tables."""
    if os.environ.get("QUOTE_AI_STORE") == "lakebase":
        from quote_ai.storage.lakebase import lakebase_db
        from quote_ai.storage.lakebase_repository import LakebaseQuoteRepository
        repo = LakebaseQuoteRepository(lakebase_db())
    else:
        from quote_ai.storage.delta_repository import DeltaQuoteRepository
        repo = DeltaQuoteRepository(sql, names["sales"])
    if os.environ.get("QUOTE_AI_READ_ONLY") == "1":
        from quote_ai.storage.lakebase_repository import ReadOnlyRepository
        repo = ReadOnlyRepository(repo)
    return repo


def _generate(service_full_name: str | None) -> Callable[[str], str] | None:
    """The chat model service (quote_ai_chat_<target>, resource chat-service) as the RAG generator."""
    if not service_full_name:
        return None
    from quote_ai.llm import endpoints, rag
    from quote_ai.llm.client import model_for
    return rag.llm_generate(endpoints.model(service_full_name.split(".")[-1]),
                            max_tokens=model_for("comparison").max_tokens)          # AI-23: the limit per use


def _ka(endpoint: str | None) -> Callable[[str], Any] | None:
    if not endpoint:
        return None
    from quote_ai.llm import knowledge_assistant
    return lambda question: knowledge_assistant.ask_ka(question, endpoint)


def _index(ai_schema: str, what: str):
    from quote_ai.search import index
    return index.get_index(ai_schema, what, os.environ.get("QUOTE_AI_SEARCH_ENDPOINT", index.ENDPOINT))


class _Lazy:
    """An AI Search index opened on first query, so starting the app needs no AI Search call."""

    def __init__(self, open_index: Callable[[], Any]) -> None:
        self._open, self._index = open_index, None

    def similarity_search(self, **kwargs):
        if self._index is None:
            self._index = self._open()
        return self._index.similarity_search(**kwargs)


# ------------------------------------------------------------------ Season 5: the agent, memory, freshness, roles

def tool_deps(ctx: Context, live_gudid: Any = None, live_first: bool = False):
    """The agent's tools on the app's own stores: the agent and the routes read the same data."""
    from quote_ai.agents.skills import SkillLibrary
    from quote_ai.agents.tools import ToolDeps
    return ToolDeps(catalog=ctx.catalog, evidence=ctx.evidence, rules=ctx.rules, sku_index=ctx.sku_index,
                    win_model=ctx.win_model, genie=ctx.genie, knowledge_index=ctx.knowledge_index,
                    accounts=ctx.accounts, live_gudid=live_gudid, skills=SkillLibrary(),
                    policy=ctx.service.policy, live_first=live_first)


def _demo_inputs_as_of(q) -> dict:
    """Local demo: every input refreshed an hour ago."""
    from datetime import datetime, timedelta, timezone
    t = datetime.now(timezone.utc) - timedelta(hours=1)
    return {"gudid": t, "evidence": t, "inventory": t, "price_book": t}


def sql_inputs_as_of(sql, names: dict) -> Callable[[Any], dict]:
    """When each input behind a quote was last refreshed: the latest release of the GUDID copy, the inventory
    snapshot and the price book (AI-02's lineage column _release_date). Verify before recording: _release_date on
    glove_devices, and whether the evidence snapshot should carry its own time (today: the GUDID copy's)."""
    from datetime import datetime, timezone

    def as_of(q) -> dict:
        row = sql.query(
            f"SELECT (SELECT max(_release_date) FROM {names['gudid_gold']}.glove_devices) AS gudid, "
            f"(SELECT max(_release_date) FROM {names['sales']}.inventory_snapshot) AS inventory, "
            f"(SELECT max(_release_date) FROM {names['sales']}.price_book) AS price_book")[0]
        out = {}
        for k, v in row.items():
            d = v if isinstance(v, datetime) else datetime.fromisoformat(str(v))
            out[k] = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        out["evidence"] = out["gudid"]
        return out
    return as_of


def _groups_from_env() -> Callable[[str], set[str]]:
    """A local run: QUOTE_AI_MANAGERS lists the managers' emails. In the workspace: quote_ai.app.backend.auth."""
    managers = {m.strip().lower() for m in os.environ.get("QUOTE_AI_MANAGERS", "").split(",") if m.strip()}
    group = os.environ.get("QUOTE_AI_MANAGERS_GROUP", "DBxLab_Sales_Managers")
    return lambda user: {group} if user.strip().lower() in managers else set()


def season5(ctx: Context, sql: Any = None, names: dict | None = None) -> Context:
    """The agent (AI-18, AI-19), memory and freshness (AI-20), roles (AI-21), on top of a Season 4 context."""
    demo = sql is None
    if ctx.agent is None:
        if not demo and os.environ.get("QUOTE_AI_AGENT_LOCAL") != "1":
            from quote_ai.agents.server import RemoteAgent
            ctx.agent = RemoteAgent.from_env()                 # AI-20: the agent on the Agent Runtime
        else:
            from quote_ai.agents import runtime
            from quote_ai.agents.agent import QuoteAgent
            from quote_ai.gudid.api_client import DemoGudidApi, LiveGudid, uc_fetch
            live = LiveGudid(DemoGudidApi(ctx.catalog) if demo else uc_fetch(sql, names["ai"]))
            client, endpoint = runtime.model_client()
            ctx.agent = QuoteAgent(deps=tool_deps(ctx, live, live_first=True), client=client, endpoint=endpoint)
    if ctx.memory is None:
        from quote_ai.agents import memory
        ctx.memory = memory.from_env(ctx.service.repo)
        ctx.service.memory = ctx.memory
    if ctx.inputs_as_of is None:
        ctx.inputs_as_of = _demo_inputs_as_of if demo else sql_inputs_as_of(sql, names)
    if ctx.as_user is None or (not demo and ctx.as_user is _demo_user_data):   # AI-24: the rep's data, as the rep
        from quote_ai.storage import user_data
        if demo:
            ctx.as_user = user_data.demo
        else:
            warehouse, space = os.environ["DATABRICKS_WAREHOUSE_ID"], os.environ.get("QUOTE_AI_GENIE_SPACE_ID")
            ctx.as_user = lambda ident: user_data.SqlUserData(ident, warehouse, names, space)
    if ctx.feedback is None:                                       # AI-22: thumbs up or down on every answer
        from . import feedback
        ctx.feedback = feedback.DemoFeedback() if demo else feedback.MlflowFeedback(sql, names["eval"])
    if ctx.groups is None:
        if demo or os.environ.get("QUOTE_AI_MANAGERS"):
            ctx.groups = _groups_from_env()
        else:
            from .auth import workspace_groups
            ctx.groups = workspace_groups()
    if ctx.ops is None:                                            # AI-26: the Ops page
        ctx.ops = _demo_ops() if demo else _sql_ops(sql, names)
    from quote_ai.agents.agent import QuoteAgent
    if os.environ.get("QUOTE_AI_DESIGN") == "specialists" and isinstance(ctx.agent, QuoteAgent):
        from quote_ai.agents.orchestrator import Orchestrator    # AI-21 design B, if decision record 0004 adopts it
        client, endpoint = ctx.agent._model()
        ctx.agent = Orchestrator(ctx.agent.deps, ctx.service, client, endpoint, ctx.groups, ctx.inputs_as_of)
    return ctx


def _demo_ops():
    """The local demo's Ops page: a week of synthetic traffic through the monitoring SQL on SQLite."""
    from quote_ai.fixtures import operations
    from quote_ai.fixtures.lakeside import NOW
    from quote_ai.ops import monitoring
    return monitoring.LocalMonitoring(operations.monitoring_data(NOW), NOW)


def _sql_ops(sql, names: dict):
    from quote_ai.ops import monitoring
    return monitoring.SqlMonitoring(sql, names)
