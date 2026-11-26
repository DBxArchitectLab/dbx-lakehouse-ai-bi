# QuoteAI

QuoteAI is the sales agent of **DBxLab Medical Supply**, a fictional glove distributor built on real public FDA GUDID
data. It is the application the DBX Architect Lab AI series builds, one episode at a time (AI-00 to AI-27). Price,
stock, quality documents and customers are synthetic demo data.

*Rules decide what must be true; AI explains, searches and drafts.* The workflow orchestrates, the agent proposes,
and only `QuoteService` commits a change to a quote.

## Isolation: one pattern finds all of QuoteAI

QuoteAI shares this repo, its bundle and its catalogs with other work. The rule, which always applies:

- **Files:** everything QuoteAI adds lives in a `quote_ai` folder in its area: `config/quote_ai/`,
  `resources/quote_ai/` (files named `quote_ai_*.yml`), `src/quote_ai/`, `tests/quote_ai/`, `scripts/quote_ai/`,
  `notebooks/quote_ai/`, `evaluation/quote_ai/` and `docs/quote_ai/`, plus this README and the release manifest
  `release/release.yml` (AI-25, its first key `quote_ai`). Find all of it with `**/quote_ai/**`.
- **Databricks objects:** names start with `quote_ai_`. That covers schemas, volumes, jobs, pipelines, serving and
  gateway endpoints, vector search endpoints, UC connections, dashboards and bundle resource keys. Apps and
  Lakebase instances allow no underscores, so they use `quote-ai-`.
- **Inside a schema (decided):** tables, views, functions and models keep plain names. The schema carries the prefix,
  so their full names already start with `quote_ai_` (`quote_ai_gudid_bronze.device_raw`,
  `quote_ai_ops.ingestion_log`); a second prefix (`quote_ai_ops.quote_ai_ingestion_log`) adds nothing.
  In a developer's personal `dev` copy, development mode puts the bundle's standard `dev_<user>_` in front of every
  resource (`dev_<user>_quote_ai_product`); `dev-shared` and `prod` use the exact `quote_ai_` names. Schemas carry
  the property `app: quote_ai`, and jobs the tag `app: quote_ai`, for cost and usage reports.
- **Enforced:** `tests/quote_ai/test_package.py` fails a pull request when a resource in `resources/quote_ai/` isn't
  prefixed, or when a QuoteAI resource sits outside that folder.

## The Lakeside transaction

Every part of the code serves one transaction, from a free-text request to an issued quote:

1. **Request**: "400 boxes of powder-free nitrile exam gloves, chemo-tested, latex-free, sizes S to XL,
   non-sterile is fine, about $8 a box, delivery in 10 days."
2. **Extracted requirements** (r1), plus what is missing: the quantity per size and the box size.
3. **Clarified and confirmed** by the rep (r2): S 60 · M 140 · L 140 · XL 60 boxes of 100; one quote line per size.
4. **Eligibility**, per stated requirement, on business truth only (verified evidence), against a named evidence
   snapshot.
5. **Fulfilment**, per line. A glove that misses the hard delivery date is an alternative that needs the customer's
   acceptance; accepting creates requirements r3 and a new quote version.
6. **Price** from the price book (`price_quote`), then the discount: Glove A lists at $8.60, Lakeside asks $8.00,
   7% off, so a manager approves that quote version.
7. **Issue**, only on fresh inputs, with every step in `quote_event`.

The five test gloves: **A** eligible and on time; **B** unknown until its chemo report is verified; **C** eligible but
late (an alternative); **D** eligible; **E** excluded (latex).

## Layout

```
src/quote_ai/
  domain/models.py           Requirements, Evidence (with its truth layer), Product, Sku, Quote, QuoteLine,
                             QuoteEvent, eligibility and fulfilment results
  rules/quote_states.py      The state machine: every allowed move, in one table (AI-21: APPROVED -> PENDING_APPROVAL
                             for a changed version)
  rules/eligibility.py       The requirement contract: one named check per stated requirement (CHECKS), three states,
                             the claims that did not count; unknown never a match (AI-15)
  rules/fulfilment.py        Full, partial, backorder or alternative date per line; late = an alternative (AI-15)
  rules/uc.py                The rules as JSON in, JSON out: what the Unity Catalog Python functions call (AI-15)
  rules/sql/check_eligibility.sql, check_fulfilment.sql  quote_ai_ai.check_eligibility / check_fulfilment (AI-15)
  rules/pricing.py           price_quote (AI-18): units from packaging, effective-dated tier row, margin floor, row ID;
                             discount bands (approval_required, discount_band, AI-21); floor_price, cost masked (AI-24)
  rules/freshness.py         Stale inputs never issue a quote; stale_details names each with its date (AI-20)
  rules/approval.py          terms_hash, check_approver (no self-approval, managers only), can_issue; no writes (AI-21)
  rules/sql/tools.sql        The agent's tools as UC functions in quote_ai_ai (AI-18)
  rules/sql/gudid_live_lookup.sql  The quote_ai_accessgudid HTTP connection and gudid_live_lookup (AI-19)
  rules/sql/lakebase_schema.sql    Lakebase (Postgres): quotes, versions, events, keys, approvals, memory (AI-20, 21)
  rules/access.py            Who sees what (AI-24): Identity, owned accounts, the cost mask; the Python twin of the SQL
  rules/sql/row_filters.sql, column_masks.sql, grants.sql  account_filter, rep_filter, mask_cost, the groups' SELECT
                             (AI-24, the quote_ai_security_setup job)
  rules/policy.py            Loads config/quote_ai/policy.yml
  rules/simple_rules.py      v1 quick quote (AI-07): exact match, list price; labelled, replaced in AI-15 to AI-21
  rules/sql/quote_tables.sql quote_ai_sales.quote, quote_line, quote_event (AI-07)
  services/quote_service.py  QuoteService: create, read_request, confirm, quick_quote, shortlist, shortlist_candidates
                             (AI-15), accept_alternative, price, submit, approve, reject, issue, change_state;
                             confirm_fact, StaleEvidence (AI-20); revise_terms, pending_approval (AI-21);
                             every move through _move
                             (ALLOWED table, optimistic locking, idempotency keys, one quote_event)
  services/shortlist.py      build_shortlist: search finds candidates, the rules decide (LocalRules / SqlRules) (AI-15)
  storage/repository.py      QuoteRepository; in memory (tests), Delta (AI-07), Lakebase (AI-20)
  storage/delta_repository.py  The Delta repository, through a SQL warehouse
  storage/lakebase.py        A DB-API transaction (psycopg on Lakebase, sqlite3 in tests); the schema applied (AI-20)
  storage/lakebase_repository.py  The Lakebase repository: one transaction per move; ReadOnlyRepository (AI-20)
  storage/lakebase_sync.sql  The read-only Delta copy of the live quotes for analytics (AI-20)
  storage/product_catalog.py What the app reads: offerable SKUs, price book, lookup by catalog number; device and
                             packaging (AI-18)
  storage/sql_files.py       Renders the SQL files with the bundle's schema names ({product}.dbxlab_sku)
  storage/accounts.py        A signed-in rep's accounts: the knowledge retrieval filter (AI-17)
  storage/user_data.py       The rep's data as the rep (AI-24): accounts, price book, Ask the data with the forwarded
                             token (SqlUserData); DemoUserData, the same rules locally
  fixtures/                  The Lakeside fixtures and Season 5 helpers (AI-22): shared by tests and the evaluation;
                             operations.py: a week of demo traffic, a glove leaving distribution, Glove B's report (AI-26)
  search/                    docs.py (the SKU search document), local.py (an in-memory index: tests, demo, suites),
                             retriever.py (hybrid + filters, variants, account filter), index.py (AI Search endpoint,
                             Delta Sync indexes), chunks.py (section-aware chunks), search_tables.sql,
                             knowledge_tables.sql (AI-15 to AI-17)
  ingest/                    The ingestion framework (AI-02): see below
  pipelines/                 GUDID silver and gold (AI-03, Lakeflow); glove_subset.sql: the glove views;
                             attributes.py: AI Functions over the glove subset -> product_evidence (AI-08);
                             documents.py: ai_parse_document, ai_extract -> supplier_quality_evidence (AI-09);
                             search_doc.sql: the SKU search document, by MERGE (AI-15)
  product/                   sku_model.sql; device_mapping.py (gloves per unit); mapping_review.py (auto-match,
                             verify, superseded, review queue)
  product/evidence.py        Evidence rows: precedence, conflicts, allowed lists (AI-08); versions, units, ranges,
                             the five checks of a supplier document (AI-09). AI-extracted is never verified
  product/documents.sql      document_registry, document_qa_signoff, supplier_price_list (AI-09)
  product/evidence_views.sql glove_evidence (all evidence, one place), product_documents (AI-09)
  product/compare.py         The comparison table from data, no score (AI-16)
  mock/catalog.py            Synthetic DBxLab catalog, reps and quote history (planted signal)
  mock/documents.py          Synthetic supplier PDFs and their truth; the demo's drops as scenarios (AI-09)
  mock/range.py              The wider glove range G to R, catalog-only, with its evidence and truth (AI-15)
  mock/knowledge.py          Playbook, GMDN notes, customer price sheets, rep accounts; the knowledge sources (AI-16)
  mock/sales.py              Synthetic sales history: quotes with every outcome, orders (AI-10)
  mock/generate_dbxlab.py    The win model's history (AI-12): two years of quotes with sent_at, the outcome and what
                             was known only afterwards; customer prices and inventory over time; the drift month (AI-14)
  ml/features.py             Label (= the win-rate metric), feature rows, point-in-time lookups, training set, time
                             split, the leak checks; Feature Engineering adapters (AI-12)
  ml/feature_tables.sql      quote_history, customer_price_history, inventory_history; quote_labels, account_features,
                             sku_features, quote_context_features, the price function, win_training_set (AI-12)
  ml/quick.py                A dependency-free logistic regression: the leak demo and the tests (AI-12)
  ml/metrics.py, baseline.py AUC, Brier, calibration; the rule baseline as a pyfunc-compatible model (AI-13)
  ml/train.py                Candidates tracked in MLflow; select (the rule written in advance), recovers_signal (AI-13)
  ml/register.py, score.py,  win_model @challenger / @champion and should_promote; nightly and what-if scoring with
  ml/monitor.py              its assertions; the inference monitor and PSI drift (AI-14)
  ml/model_tables.sql        model_selection, model_promotion, open_quote_scores (AI-13, AI-14)
  semantic/                  quote_metrics.sql, order_metrics.sql (metric views), sales_tables.sql,
                             sales_overview.lvdash.json + dashboard.py (lint, embed), alerts.sql (AI-10);
                             comments.sql, genie/ (sales_analyst.yml, deploy_genie.py, client.py) (AI-11);
                             operations.lvdash.json: the operating dashboard on the Ops page's tables (AI-26)
  jobs/product_job.py        setup, seed, match, verify, grant-app (the quote_ai_product_model job)
  jobs/documents_job.py      setup, generate, signoff, views (quote_ai_mock_documents, AI-09)
  jobs/semantic_job.py       setup, seed, metrics, comments (quote_ai_semantic, quote_ai_genie; AI-10, AI-11)
  jobs/ml_job.py             setup, seed, features, training-set, train, register, score, monitor, drift (Season 3)
  jobs/search_job.py         setup, seed, doc, index, functions, grants (quote_ai_search_index, AI-15)
  jobs/knowledge_job.py      setup, accounts, chunk, index, grants (quote_ai_knowledge_index, AI-16)
  jobs/eval_job.py           load: the cases into quote_ai_eval (quote_ai_eval_tables, AI-17)
  jobs/agent_job.py          functions, register, grants (quote_ai_agent_registration, AI-18, AI-19)
  jobs/lakebase_job.py       schema, migrate, sync, purge (quote_ai_lakebase, quote_ai_lakebase_migrate, AI-20)
  jobs/security_job.py       filters, masks, grants, audit (quote_ai_security_setup, AI-24)
  jobs/release_job.py        check, promote, smoke (quote_ai_promote, quote_ai_smoke_test, AI-25)
  jobs/ops_job.py            setup, freshness, cost, score, alerts, grants (quote_ai_monitors, quote_ai_gudid_daily,
                             quote_ai_live_scoring, AI-26)
  agents/                    Season 5: tools.py (specs and executors), reliability.py (timeouts, retries, Turn),
                             loop_by_hand.py (the loop), agent.py (QuoteAgent, an MLflow ResponsesAgent),
                             boundary.py (answers checked against the tools), standin.py (the stand-in model),
                             runtime.py (AI-18); mcp.py, skills/quoting-playbook/ (AI-19); memory.py, server.py
                             (the Agent Runtime, AI-20); orchestrator.py, specialists/ (AI-21); grants.py, per-tool
                             grants checked against config/quote_ai/tool_grants.yml (AI-24)
  agent_app/                 The agent's own Databricks App on the Agent Runtime: app.yaml, main.py (AI-20)
  gudid/api_client.py        AccessGUDID v3 lookup of ONE device, cached a day per DI (AI-19)
  ops/migrate_quotes_to_lakebase.py  Delta -> Lakebase: copy, prove counts and checksums, switch (AI-20)
  ops/promote.py             The release manifest: validate, plan, promote (champion aliases, target catalog only,
                             idempotent), check; MlflowRegistry (AI-25)
  ops/smoke_test.py          After a deploy: health, then the Lakeside request through the app's API to PRICED (AI-25)
  ops/freshness.sql, cost.sql, monitoring_tables.sql  quote_ai_monitoring: freshness per source and input,
                             trace_usage, cost_per_quote by step (app compute spread), live_scores, slo_daily (AI-26)
  ops/monitoring.py          freshness_status, inputs_as_of, slo_daily (p95), cost_by_step, ops_view; run_local (the
                             same SQL on SQLite); LocalMonitoring, SqlMonitoring (AI-26)
  ops/live_scoring.py        The gate's scorers on sampled production traces; usage rows (AI-26)
  llm/client.py              One measured call: text, tokens, latency (AI-01, AI-04); model_for(use) from
                             config/quote_ai/models.yml, cost_usd (AI-23)
  llm/cache.py, llm_cache.sql  The model-call cache (AI-23): keyed by prompt, model and GUDID publicVersionNumber; never
                             price, stock or account data
  llm/compare_models.py      Model comparison on the product facts suite (AI-04)
  llm/requirements_schema.py The Requirements schema; to_domain gives r1 (AI-05)
  llm/prompts.py, extract.py The versioned prompt (Prompt Registry) and extract_requirements (AI-05); v4, the footnote
                             rule (AI-22); load_prompt by alias, champion/challenger, no fallback; compare_explain v4
                             (AI-23)
  llm/endpoints.py           Model service names per use case and target (AI-06)
  llm/gateway_checks.py      The Model access suite's outcomes (AI-06)
  llm/attributes.py          The glove attribute form: one schema for ai_query and the chat call (AI-08)
  llm/rag.py                 explain_comparison: retrieve, generate, ground, cite; "no evidence on file" (AI-16)
  llm/knowledge_assistant.py The buy option: Agent Bricks Knowledge Assistant, as the same statements (AI-16)
  storage/evidence_store.py  What the app reads: evidence, documents; a request PDF into the volume (AI-08, AI-09)
  app/                       QuoteAI v1 (AI-07): backend/ (FastAPI), frontend/ (React + Vite), app.yaml;
                             Season 2 routers and pages: evidence + Product (AI-08), documents + PDF upload and
                             Documents tab (AI-09), pipeline + My pipeline (AI-10), ask_data + Ask the data (AI-11);
                             Season 3: win_chance + the Quote page, the win chance at three prices (AI-14);
                             Season 4: shortlist + Shortlist (AI-15), compare + Compare, v2 (AI-16, AI-17);
                             Season 5: chat + ChatPanel, streamed (AI-18), SourceBadge (AI-19), My quotes from
                             Lakebase and resume (AI-20), price + Pricing, approvals + Approval inbox by role, v3 (AI-21);
                             Season 6: feedback + Feedback (thumbs on every answer, AI-22); backend/deps.py: the back
                             end queries as the signed-in rep, the code filter by rep deleted (AI-24);
                             Season 7: ops + the Ops page for managers, Compare's fallback without prose (AI-26)
config/quote_ai/policy.yml   Discount bands and freshness limits
config/quote_ai/models.yml   The model service and token limit per use, and their prices (AI-23)
config/quote_ai/tool_grants.yml  Which specialist may EXECUTE which tool function (AI-24)
config/quote_ai/slo.yml      The SLOs: freshness 24 h, p95 20 s, quality, cost per quote; the watched sources (AI-26)
release/release.yml          The release manifest: QuoteAI 1.0.0, the evaluation package, model and prompt versions (AI-25)
config/quote_ai/ingest/sources.yml  The ingestion config: one entry per source
resources/quote_ai/          quote_ai_gudid pipeline, quote_ai_product_model job, model services (extract, chat,
                             embed), quote_ai_app and its grants job, volume and traces experiment;
                             quote_ai_attributes pipeline (AI-08); quote_ai_documents pipeline,
                             quote_ai_mock_documents and quote_ai_document_signoff jobs (AI-09);
                             quote_ai_semantic job, quote_ai_sales_overview dashboard (AI-10); quote_ai_genie job (AI-11);
                             quote_ai_ml_features job (AI-12); quote_ai_train job, quote_ai_win experiment (AI-13);
                             quote_ai_ml_register and quote_ai_ml_score jobs, quote_ai_win_model endpoint (AI-14);
                             quote_ai_search_index job, quote_ai_packages volume (AI-15); quote_ai_knowledge_index
                             job (AI-16); quote_ai_eval_tables job, quote_ai_eval experiment (AI-17);
                             quote_ai_agent_registration job (AI-18); the accessgudid connection notes (AI-19);
                             quote_ai_lakebase database catalog and jobs, quote_ai_agent app (AI-20); quote_ai_eval
                             job (AI-22); quote_ai_experiment job, quote_ai_chat_small (AI-23); quote_ai_security_setup
                             job, the app's user_api_scopes, gateway guardrails notes (AI-24); quote_ai_promote,
                             quote_ai_rules_setup, quote_ai_smoke_test jobs (AI-25); quote_ai_gudid_daily (runs the
                             existing jobs in order), quote_ai_live_scoring, quote_ai_monitors jobs, quote_ai_operations
                             dashboard (AI-26); quote_ai_capstone job (AI-27); notes on the AI Search
                             index and the Knowledge Assistant (*.notes.md)
tests/quote_ai/              The Lakeside transaction end to end, the requirement contract, every refusal; ingestion;
                             mapping, models, requirements, gateway, states, Delta repository, the app's API;
                             evidence, documents, metric definitions, Genie, the Season 2 routes; the Win model
                             suite: features (point in time, leakage), train (selection rule), score, win chance route;
                             Season 4: eligibility, fulfilment, search, compare, scorers, the Season 4 routes;
                             Season 5: pricing, tools, agent boundary, reliability, API client, tool access, memory,
                             freshness, Lakebase, approval, routing, transactions, the Season 5 routes and suites;
                             Season 6: the gate, feedback (AI-22), the cache and experiments (AI-23), security and the
                             injected email (AI-24); Season 7: the release manifest, promotion, smoke test (AI-25),
                             operations and the Ops page (AI-26), the capstone end to end (AI-27)
evaluation/quote_ai/run_eval.py  The runner: --suite requirements | mapping | product_facts | genie | eligibility |
                             fulfilment | search | comparison | pricing | reliability | workflow | ingestion |
                             win_model | model_access | data_authorization, --variant, --mlflow, --design, --kinds;
                             --suites ci | all | a,b: the gate, exit 1 below a threshold, results table (AI-22)
evaluation/quote_ai/gate.py  The release rule: thresholds, check (pass, fail, not_run), verdict, result rows (AI-22)
evaluation/quote_ai/security.py  The Data authorization suite: sec-01 to sec-05 as named identities (AI-24)
evaluation/quote_ai/operations.py  The Operations suite, required from AI-26: ops-01 to ops-08
evaluation/quote_ai/capstone/  app_flow.py (the Lakeside transaction through the API), run_capstone.py (the scorecard
                             against the plan's success criteria) (AI-27)
evaluation/quote_ai/scorers.py, season4.py  Season 4 scorers (gates and metrics) and predict functions (AI-17)
evaluation/quote_ai/season5.py  Season 5 suites: pricing, reliability scenarios, the agent workflow (AI-18 to AI-21)
evaluation/quote_ai/experiments/0004_one_vs_specialists.py  One agent vs specialists, the rule written first (AI-21)
evaluation/quote_ai/experiments/run_experiment.py  One variable per run, scores + cost per quote + p95, ship (AI-23)
evaluation/quote_ai/v1/      Suites: ingestion, mapping, product_facts (100 gloves + documents), requirements,
                             model_access, genie (40 questions); eligibility (60), fulfilment, search (50),
                             comparison (30) (AI-15 to AI-17); pricing (23), reliability (16), workflow (23 + 100
                             end to end) (AI-18 to AI-21); thresholds.yml (16 suites) and VERSION, requirements 100,
                             elig-007, ingestion, win_model, operations (AI-22); search held-out slice (AI-23);
                             data_authorization (5), model_access + sec-06, sec-07 (AI-24); operations (8),
                             package 1.3.0 (AI-26)
notebooks/quote_ai/          01_first_call, 04_model_comparison, 05_requirements, 06_gateway_tests; 12_ml_features,
                             13_automl, 14_drift_demo; 15_search
scripts/quote_ai/build_app.py  The app folder before deploy: React build, vendored quote_ai package
scripts/quote_ai/win_model_smoke.py  CD: the @champion version for the deploy, and the endpoint's smoke query (AI-14)
docs/quote_ai/data-sources.md  Every source, its terms, where it lands, the audit trail; silver, gold, product model
docs/quote_ai/decisions/     0001 model per job (AI-04), 0002 gateway per use case (AI-06), 0003 win-rate
                             definition (AI-10), adr-win-model-selection (AI-13), 0003-rag-build-or-buy (AI-16),
                             0004 orchestration and approval (AI-21), 0005 optimization (AI-23), 0006 threat model
                             (AI-24); 0007 to 0012 written in AI-27 (data, SKU model, states and approval, evidence,
                             eligibility, pricing)
docs/quote_ai/architecture.md, deployment-guide.md, retrospective.md  QuoteAI 1.0 (AI-27)
docs/quote_ai/runbooks/      release-and-rollback.md (AI-25), incidents.md (AI-26)
docs/quote_ai/ai-08-.../ to ai-27-.../  Each Season 2 to 7 episode: files, deploy and run, what to verify
```

## Ingestion (AI-02)

One config, one engine. [`config/quote_ai/ingest/sources.yml`](config/quote_ai/ingest/sources.yml) says **what** to load: the
source, the format, where it lands, the target tables, their keys and the schedule. `src/quote_ai/ingest` knows
**how**. Adding a source is one config entry in one pull request.

| Source | Pattern | Bronze |
| --- | --- | --- |
| `gudid_full_release` | Bulk: the AccessGUDID delimited full release, once | `quote_ai_gudid_bronze.device_raw`, `identifiers_raw`, ... |
| `gudid_daily_updates` | Updates: daily release files, Auto Loader | `quote_ai_gudid_bronze.daily_device_raw` |
| `gudid_api_device_history` | API: AccessGUDID v3, only for what changed | `quote_ai_gudid_bronze.api_device_history` |
| `dbxlab_synthetic` | Generated: DBxLab price book, stock, customers | `quote_ai_sales_bronze.*_raw` |

```
ingest/config.py           The config schema (pydantic, strict): checked in CI; config_hash
ingest/download.py         Landing zone: release zips into a Volume, <landing>/<yyyymmdd>/, never edited;
                           replays are no-ops, a re-published file is refused; every file in quote_ai_ops.ingestion_file
ingest/http.py             Rate limit, retries with backoff on 429/5xx (Retry-After), clear errors
ingest/gudid_client.py     AccessGUDID v3 device history, one row per published version
ingest/api_source.py       PySpark custom data source "quote_ai_gudid_api": the client on the workers
ingest/bronze_pipeline.py  Lakeflow pipeline: Auto Loader -> snake_case -> lineage -> expectations -> AUTO CDC by key
ingest/engine.py           land -> (pipeline) -> record -> api; watermarks, the request cap, the run log
ingest/log.py              quote_ai_ops.ingestion_log, quote_ai_ops.ingestion_file, quote_ai_ops.ingestion_config, quote_ai_ops.v_ingestion_audit
ingest/checks.py           The bronze checks: one row per key, _load_id present and logged
ingest/spark_io.py         The Spark side: MERGE, the ops tables (Databricks only)
ingest/bundle_gen.py       Generates resources/quote_ai/quote_ai_ingest*.yml from the config (one job per file)
jobs/ingest_job.py         The job entry point: setup, land, record, api, checks
```

**Guarantees.**
- **Lineage:** every bronze row carries `_source`, `_file_or_request`, `_load_id`, `_ingested_at` and
  `_release_date`.
- **Replays never duplicate:** a load ID is deterministic (the same file always gets the same ID), and bronze is
  keyed (AUTO CDC for files, MERGE for the API).
- **Every run is logged:** each run gets a row in `quote_ai_ops.ingestion_log`. A failed run is logged as failed, with its
  error.

**After changing the config:**

```
python scripts/quote_ai/gen_ingest_resources.py   # regenerate resources/quote_ai/quote_ai_ingest*.yml
python -m pytest -q tests                       # config, Ingestion suite cases, generated files up to date
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_ingest_once --params release_date=2026-11-02   # the full release, once
databricks bundle run -t dev quote_ai_ingest_daily
databricks bundle run -t dev quote_ai_ingest_on_demand                                # DBxLab synthetic files
```

**Verify in the workspace** before the first run, and before recording AI-02. Each item is marked in the code and
config:
- the files inside the AccessGUDID zips and their column names (and so the keys);
- the daily update format and its row tag;
- whether the v3 history API accepts `record_key`;
- Lakeflow's module name (`dlt` or `pyspark.pipelines`), AUTO CDC on equal sequence values, and pipelines writing
  to several schemas;
- the `{{tasks.files.result_state}}` job value reference;
- PySpark custom data sources on serverless.

## Run the tests

```
python -m pip install -e ".[dev]" databricks-sdk
python -m pytest -q tests
```

No Databricks connection is needed: the rules and `QuoteService` are plain Python, tested with an in-memory store.
From Season 5 the agent runs on a deterministic stand-in model (`quote_ai.agents.standin`) and the Lakebase
repository on SQLite running the same statements; `QUOTE_AI_DEMO=1` runs the app and the agent locally that way.

## Implementation plan

The code is built by architecture part, contracts first, one episode ahead of recording. Each episode is one pull
request (`feature/ai-NN-<slug>`), tagged `ai-NN` when the episode is published.

| Phase | What | Status |
| --- | --- | --- |
| 0 Feasibility spike | One runnable check per risky dependency in the Azure region: Apps with user authorization, Unity Gateway, Lakebase and a synced table, Agent Runtime, a PySpark custom data source, AccessGUDID v3, Agent Bricks. Output: `docs/quote_ai/feature-availability.md` | To do |
| 1 Contracts and fixtures | Domain models, state machine, eligibility, fulfilment, pricing, freshness, QuoteService on an in-memory repository, the Lakeside fixtures and tests | **Done (this branch)** |
| 2 Walking skeleton | Thin but real, deployed to dev-shared: the ingestion framework (AI-02, **built, workspace run to do**), the product model with verified SKU mappings (AI-03), model comparison (AI-04), the extraction behind Unity Gateway (AI-05, AI-06), QuoteService on Delta, FastAPI + React with the four v1 screens (AI-07): **built, workspace run to do** | **In progress** |
| 3 Each part made real | Evidence (AI-08, 09), analytics (AI-10, 11), win model (AI-12 to 14: **built, workspace run to do**), search and rules as UC function wrappers (AI-15 to 17: **built, workspace run to do**), the agent, Lakebase and approval (AI-18 to 21: **built, workspace run to do**), the evaluation gate, optimization, user authorization (AI-22 to 24: **built, workspace run to do**), release and operations (AI-25, 26: **built, workspace run to do**), the capstone (AI-27: **built, deployment rehearsal to do**) | Later |

Rules stay in Python: Unity Catalog functions and the agent's tools are thin wrappers that call `quote_ai.rules`, so
SQL, the agent and pytest run the same code.
