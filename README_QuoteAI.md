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
  `notebooks/quote_ai/`, `evaluation/quote_ai/` and `docs/quote_ai/`, plus this README. Find all of it with
  `**/quote_ai/**`.
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
  rules/quote_states.py      The state machine: every allowed move, in one table
  rules/eligibility.py       The requirement contract: one check per stated requirement; unknown never a match
  rules/fulfilment.py        Full, partial or backorder per line; late = an alternative
  rules/pricing.py           Units, price book rows, margin floor (price_quote); discount bands (approval_required)
  rules/freshness.py         Stale inputs never issue a quote
  rules/approval.py          The terms hash an approval is bound to
  rules/policy.py            Loads config/quote_ai/policy.yml
  services/quote_service.py  QuoteService: create, confirm, shortlist, accept_alternative, price, submit, approve,
                             reject, issue; optimistic locking, idempotency keys, quote_event
  storage/repository.py      QuoteRepository; in memory now, Delta (AI-07) and Lakebase (AI-20) later
  ingest/                    The ingestion framework (AI-02): see below
config/quote_ai/policy.yml   Discount bands and freshness limits
config/quote_ai/ingest/sources.yml  The ingestion config: one entry per source
tests/quote_ai/              The Lakeside transaction end to end, the requirement contract, every refusal; ingestion
evaluation/quote_ai/v1/ingestion/  The Ingestion suite: bad configs that CI must refuse, and the bronze checks
docs/quote_ai/data-sources.md  Every source, its terms, where it lands, the audit trail
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

## Implementation plan

The code is built by architecture part, contracts first, one episode ahead of recording. Each episode is one pull
request (`feature/ai-NN-<slug>`), tagged `ai-NN` when the episode is published.

| Phase | What | Status |
| --- | --- | --- |
| 0 Feasibility spike | One runnable check per risky dependency in the Azure region: Apps with user authorization, Unity Gateway, Lakebase and a synced table, Agent Runtime, a PySpark custom data source, AccessGUDID v3, Agent Bricks. Output: `docs/quote_ai/feature-availability.md` | To do |
| 1 Contracts and fixtures | Domain models, state machine, eligibility, fulfilment, pricing, freshness, QuoteService on an in-memory repository, the Lakeside fixtures and tests | **Done (this branch)** |
| 2 Walking skeleton | Thin but real, deployed to dev-shared: the ingestion framework (AI-02, **built, workspace run to do**), about 10 verified SKU mappings (AI-03), one extraction endpoint behind Unity Gateway (AI-05, AI-06), QuoteService on Delta, FastAPI + React with the four v1 screens (AI-07) | **In progress** |
| 3 Each part made real | Evidence (AI-08, 09), analytics (AI-10, 11), win model (AI-12 to 14), search and rules as UC function wrappers (AI-15 to 17), the agent (AI-18 to 21), the evaluation gate, optimization, user authorization, release and operations (AI-22 to 26) | Later |

Rules stay in Python: Unity Catalog functions and the agent's tools are thin wrappers that call `quote_ai.rules`, so
SQL, the agent and pytest run the same code.
