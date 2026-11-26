# AI-10: The semantic layer, metric views and AI/BI dashboards

DBxLab's sales measures are defined once, next to the data, in two metric views; the AI/BI dashboard, the SQL
editor, Genie (AI-11) and the agent query them with `MEASURE()`. Change a definition in one file and every consumer
follows. Win rate: won over won, lost and expired; cancelled excluded
([decision 0003](../decisions/0003-win-rate-definition.md)).

| File | What |
| --- | --- |
| `src/quote_ai/semantic/quote_metrics.sql` | Metric view: quotes, win_rate, avg_discount_pct, quote_turnaround_h, approval_rate |
| `src/quote_ai/semantic/order_metrics.sql` | Metric view: orders, units, revenue, avg_unit_price, margin_pct |
| `src/quote_ai/semantic/sales_tables.sql` | `quote_fact` and `order_line`: the row-level sources |
| `src/quote_ai/mock/sales.py` | Synthetic history: AI-03's closed quotes plus expired, cancelled and open ones; an order per won quote |
| `src/quote_ai/semantic/sales_overview.lvdash.json` | The dashboard: KPIs, pipeline by state, win rate by region and month, what the account bought |
| `src/quote_ai/semantic/dashboard.py` | The lint (datasets query metric views only, no catalog) and the embed URL |
| `src/quote_ai/semantic/alerts.sql` | The two SQL alerts (nitrile discount above 12%, slow turnaround) |
| `src/quote_ai/jobs/semantic_job.py` | setup, seed, metrics (and comments, AI-11) |
| `resources/quote_ai/quote_ai_semantic.job.yml` | `quote_ai_semantic` |
| `resources/quote_ai/quote_ai_sales_overview.dashboard.yml` | The dashboard, viewer credentials, Sales groups CAN_RUN |
| `src/quote_ai/app/frontend/src/pages/MyPipeline.tsx`, `backend/routers/pipeline.py` | My pipeline: the dashboard embedded |
| `tests/quote_ai/test_metric_definitions.py` | Known answers (0.40 at every grain), no average of averages, the lint |

## Deploy and run

```
databricks bundle deploy -t dev --var quote_ai_warehouse_id=<id>
databricks bundle run quote_ai_semantic -t dev
databricks bundle summary -t dev               # the dashboard link
```

The demo's definition change: in `quote_metrics.sql` change the win_rate denominator to `('WON', 'LOST')`, deploy,
run `quote_ai_semantic`; the dashboard and the SQL editor both move. Change it back (decision 0003).

Alerts: until bundle support is verified, save each query in `alerts.sql` (rendered for the target) as a SQL alert,
with the condition in its comment, owned by Sales Ops.

## Design notes

- The metric views read `quote_fact`, not the app's `quote` table: live quotes have no outcome yet and join the
  metrics in AI-20 with the Lakebase-to-Delta sync. The script says the source is `quote_ai_sales.quote`.
- `warehouse_id` uses the existing variable `quote_ai_warehouse_id` (the draft names a new `warehouse_id`).
- The bundle job runs Python tasks that render the SQL files (like every QuoteAI job), not a SQL task, so a personal
  dev copy's schema names work unchanged.
- My pipeline: the back end only says which dashboard (`QUOTE_AI_DASHBOARD_ID`, else found by display name); the
  page embeds it. The rep's own accounts: a row filter in AI-24; until then the account filter on the dashboard.

## Verify before recording

- Metric view YAML (version 1.1 for comments, `COMMENT` before `AS`, a SQL query as `source`, `FILTER` in a measure).
- Dashboard bundle keys `dataset_catalog`, `dataset_schema`, `embed_credentials`; the widget JSON (open it in the UI
  after the first deploy and export it back if the editor rewrote it).
- Embedding: the app's domain in the allowed embedding domains, and how the embedded dashboard signs the viewer in.
- On screen: quote and sales data are synthetic demo data.
