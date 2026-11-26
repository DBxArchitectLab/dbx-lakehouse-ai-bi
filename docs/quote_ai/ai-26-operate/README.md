# AI-26: Operate - freshness, monitoring and cost

Production isn't a finish line. A daily job carries GUDID's changes all the way to what the rep is offered; freshness
is measured on the data's age, per source and per input, and is also a rule at issue time; the gate's own scorers run
on live traces; cost per quote is split by step, including the app's own compute; one view feeds the app's Ops page
and the operating dashboard; and the incident runbook is in the repo.

| File | What |
| --- | --- |
| `resources/quote_ai/quote_ai_gudid_daily.job.yml` | ingest (runs `quote_ai_ingest_daily`, unchanged) -> gudid -> evidence, documents -> search_doc -> index_sync -> freshness |
| `src/quote_ai/ops/freshness.sql` | `quote_ai_monitoring.freshness`: hours since the last successful run per source (the AI-02 ingestion log), documents waiting for evidence, the age of each input QuoteService checks |
| `src/quote_ai/ops/cost.sql` | `trace_usage`, `model_prices`, `daily_compute` (billing system tables), `cost_per_quote` by step with the app's compute spread over the day's quotes |
| `src/quote_ai/ops/monitoring_tables.sql` | `live_scores`, `slo_daily` |
| `src/quote_ai/ops/monitoring.py` | `freshness_status`, `breaches`, `inputs_as_of`, `slo_daily` (p95), `cost_by_step`, `ops_view`; `run_local` (the same SQL on SQLite); `LocalMonitoring`, `SqlMonitoring` |
| `src/quote_ai/ops/live_scoring.py` | `sample`, `score` with the gate's scorers (imported from `evaluation/quote_ai/scorers.py`), `usage_rows`, `run`; `search_traces`, `mlflow_judge` (workspace adapters) |
| `src/quote_ai/jobs/ops_job.py` | setup, freshness, cost, score, alerts, grants |
| `resources/quote_ai/quote_ai_live_scoring.job.yml`, `quote_ai_monitors.yml` | Daily live scoring and cost; the monitoring setup, the two SQL alerts, the read grants |
| `src/quote_ai/semantic/operations.lvdash.json`, `resources/quote_ai/quote_ai_operations.dashboard.yml` | The operating dashboard on the same three objects as the Ops page |
| `config/quote_ai/slo.yml` | The SLOs: freshness 24 h, p95 under 20 s for 95%, zero violations, grounded 0.90, cost per quote; the watched sources |
| `src/quote_ai/app/backend/routers/ops.py`, `frontend/src/pages/Ops.tsx` | `/api/ops` (managers only) and the Ops page; `?quote_id=` gives one quote's cost |
| `src/quote_ai/app/backend/routers/compare.py`, `pages/Compare.tsx` | The comparison model fails: the table and the rules' findings without prose, logged, never cached |
| `src/quote_ai/fixtures/operations.py` | A week of demo traffic; a glove leaving distribution; Glove B's report arriving |
| `evaluation/quote_ai/operations.py`, `v1/operations/` | The Operations suite, required from here (ops-01 to ops-08); package 1.3.0 |
| `docs/quote_ai/runbooks/incidents.md` | Symptom, first check, action; the owner |
| `tests/quote_ai/test_operations.py`, `test_app_season7.py` | The SQL on fixtures, SLOs, live scoring, the jobs, the dashboard, the suite; the Ops route and the fallback |

## Deploy and run

```
databricks bundle deploy -t dev
databricks bundle run quote_ai_monitors -t dev          # tables, views, prices, alerts, grants
databricks bundle run quote_ai_gudid_daily -t dev
databricks bundle run quote_ai_live_scoring -t dev
python evaluation/quote_ai/run_eval.py --suite operations
QUOTE_AI_DEMO=1 QUOTE_AI_MANAGERS=mgr.maria@dbxlab.example ...   # the Ops page on the demo week
```

## Verify before recording

- AccessGUDID daily update file format and publication time (the 07:00 schedule).
- `run_job_task` with `${resources.jobs.quote_ai_ingest_daily.id}`; `run_if: ALL_DONE`.
- MLflow production monitoring: `mlflow.search_traces` (filter, return type), the trace tags the app sets (quote ID,
  step, service: today the traces don't carry `quote_ai.step` / `quote_ai.kind`; add them where QuoteService and the
  routes open spans), token usage attributes, `mlflow.genai.evaluate` with the guideline judge.
- System tables: `system.billing.usage` and `list_prices` columns; whether the app and the serving endpoint carry the
  tag `app: quote_ai`; that pay-per-token calls are not also billed under these products (no double count).
- The SQL alerts API (`w.alerts.create`) or an `alerts` bundle resource in the current CLI.
- Data and model quality monitoring for the win model (AI-14's monitor): current name and API.
- AI Search index sync (triggered) cost; Lakeflow pipeline triggers from a job.

## Differences from the draft

- **Schedule.** The draft removes the 06:00 trigger of the AI-02 ingestion job so this job owns it. That trigger is
  generated from `config/quote_ai/ingest/sources.yml` (the ingestion framework, not changed here). So the daily job
  runs at 07:00 and its ingest task, a replay, finds nothing new; the script's "six UTC" becomes seven until the
  ingestion config drops its own schedule (a one-line change to propose separately).
- **Evidence and index tasks** run the existing pipelines (`quote_ai_attributes`, `quote_ai_documents`) and
  `search_job.py doc / index`, not `product/evidence.py` and `search/index.py --sync` as files (those are modules, not
  entry points).
- **Supplier documents freshness** is "the oldest signed-off document not yet evidence", not "the newest evidence
  row": documents arrive rarely, so the newest row's age would page every day.
- **The excluded list.** With the index synced, the distribution pre-filter (AI-15) drops the glove from the
  candidates, so it is not offered and no longer listed. It appears in the excluded list, with `in_distribution`
  verified_mismatch and the new GUDID record version, when the evidence is updated before the index catches up (ops-01
  checks both). For the demo, refresh the Shortlist between the evidence and index_sync tasks.
- **elig-012a / elig-012b** are the eligibility cases E-014 (Glove B, snapshot v41) and E-015 (v57) in this repo.
- **Operations** is required in the gate on its fixture cases; production's own numbers (freshness now, p95, live
  grounded rate) are watched on the Ops page and alerts, not gated. Monitoring lives in `quote_ai_monitoring` (the
  draft's `${catalog}.monitoring`).
- The Ops page shows cost per quote by step and one quote's cost; app logs are in Apps > Logs (not in the page).
