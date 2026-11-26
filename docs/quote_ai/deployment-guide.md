# QuoteAI deployment guide: from an empty workspace to QuoteAI

Every step is a bundle deploy or a bundle job; nothing is created by hand except the prerequisites. Start in your own
`dev` copy (`-t dev`, schedules paused, names prefixed `dev_<user>_`), then the shared `dev-shared` and `prod` through
GitHub Actions only (`docs/quote_ai/runbooks/release-and-rollback.md`).

## 0. Prerequisites (once, by an admin)

- The catalogs `dbxarchitectlab_dev` / `_prod` (dbx-platform-infra-azure bootstrap); a SQL warehouse ID per target
  (`--var quote_ai_warehouse_id=...`); the Lakebase instance `quote-ai-db`.
- Account groups `DBxLab_Sales_Reps`, `DBxLab_Sales_Managers` (demo users only), `DBX_Architect_Lab_Admin`.
- The service principal `sp-dbx-lakehouse` with its OAuth secret in the GitHub environments `dev` and `prod`; prod
  environment reviewers.
- Region availability checked: Apps with user authorization, Unity Gateway model services, Lakebase, Agent Runtime,
  AI Search, AI Functions (`docs/quote_ai/feature-availability.md`, phase 0).
- AccessGUDID terms read (`docs/quote_ai/data-sources.md`).

## 1. Build and deploy the bundle

```
python -m pip install -e ".[dev]" databricks-sdk
python -m pytest -q tests                      # green apart from the wheel build test (needs the CLI)
python scripts/quote_ai/build_app.py           # React build + the vendored package (Node 20)
databricks bundle validate -t dev
databricks bundle deploy -t dev --var quote_ai_warehouse_id=<id>
```

## 2. Data, in order

| # | Run | What it creates |
| --- | --- | --- |
| 1 | `quote_ai_ingest_once --params release_date=<yyyy-mm-dd>` | GUDID full release into bronze (AI-02) |
| 2 | `quote_ai_ingest_daily`, `quote_ai_ingest_on_demand` | A daily file, the API lookups; DBxLab synthetic files |
| 3 | `quote_ai_product_model` | Silver, gold, the SKU model, mappings (demo-verified) |
| 4 | `quote_ai_attributes` (pipeline), `quote_ai_mock_documents` | product_evidence; supplier documents and their evidence |
| 5 | `quote_ai_semantic`, `quote_ai_genie` | Metric views, the dashboard's data, the Genie Agent (set `quote_ai_genie_space_id`, deploy again) |
| 6 | `quote_ai_ml_features`, `quote_ai_train`, `quote_ai_ml_register`, `quote_ai_ml_score` | The win model @champion and its endpoint |
| 7 | `quote_ai_search_index`, `quote_ai_knowledge_index` | AI Search indexes, the rule UC functions |
| 8 | `quote_ai_eval_tables`; `python -m quote_ai.llm.prompts --catalog <c> --alias champion` | Eval tables; prompts registered |
| 9 | `quote_ai_agent_registration`, `quote_ai_lakebase` (schema), `quote_ai_lakebase_migrate` | Agent tools and model; Lakebase quotes |

## 3. The app, security and rules

| # | Run | What it does |
| --- | --- | --- |
| 10 | `databricks bundle run quote_ai_app -t dev` (and `quote_ai_agent`) | Deploys the app's source |
| 11 | `quote_ai_app_grants`, `quote_ai_security_setup` (or `quote_ai_rules_setup`) | Least privilege; filters, masks, grants |
| 12 | `quote_ai_monitors` | Monitoring tables and views, model prices, alerts, read grants (AI-26) |
| 13 | `quote_ai_gudid_daily`, `quote_ai_live_scoring` | The daily chain and live scoring (scheduled in prod) |

## 4. Prove it

```
databricks bundle run quote_ai_eval -t dev                          # the gate: all sixteen suites
databricks bundle run quote_ai_smoke_test -t dev                    # the Lakeside request through the API
databricks bundle run quote_ai_capstone -t dev --params baseline_minutes=<by hand>,app_minutes=<in the app>
```

## 5. Release to prod

Set `release/release.yml`, open a pull request (CI, full eval), merge, approve: `cd.yml` validates, checks the
manifest's versions exist in prod, deploys, runs the rules setup, promotes the aliases, deploys the app and runs the
smoke test. Before the first prod release, the manifest's model and prompt versions must exist in the prod catalog
(register them there; `python -m quote_ai.ops.promote --catalog dbxarchitectlab_prod --check`).
