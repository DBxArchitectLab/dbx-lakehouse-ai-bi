# AI-14: Register, serve and monitor

The win model becomes a governed service: registered in Unity Catalog with aliases, every open quote scored nightly,
an endpoint that scales to zero for what-if prices, promotion by a reviewed job, and a monitor that catches drift.
The rep sees Lakeside's win chance at three prices on the Quote page, labelled **simulation, demo data**. The
prediction informs the rep; it never sets the price (the price book and the policy do, `quote_ai.rules.pricing`).

| File | What |
| --- | --- |
| `src/quote_ai/ml/register.py` | Register the selected run as `quote_ai_ml.win_model`, @challenger; `should_promote`; the endpoint follows @champion |
| `src/quote_ai/ml/score.py` | Scoring rows (`request_features`), the alias-only URI, `score_batch`, the what-if call, the run's assertions |
| `src/quote_ai/ml/monitor.py` | The inference monitor (data and model quality monitoring); PSI drift in plain Python |
| `src/quote_ai/ml/model_tables.sql` | `model_promotion`, `open_quote_scores` (change data feed on, for the monitor) |
| `resources/quote_ai/quote_ai_ml_register.job.yml` | `quote_ai_ml_register` |
| `resources/quote_ai/quote_ai_ml_score.job.yml` | `quote_ai_ml_score`, nightly: score, then the monitor |
| `resources/quote_ai/quote_ai_win_model.serving.yml` | `quote_ai_win_model_<target>`: Small, scale to zero, inference table |
| `resources/quote_ai/quote_ai_app.app.yml` | + the endpoint as an app resource, CAN_QUERY (`win-model`) |
| `src/quote_ai/app/backend/routers/win_chance.py` | `POST /api/win-chance`: one call, several prices, read-only |
| `src/quote_ai/app/frontend/src/pages/Quote.tsx` | The Quote page: lines, three prices, the win chance, the label |
| `scripts/quote_ai/win_model_smoke.py` | CD: the @champion version for the deploy, and the smoke query |
| `notebooks/quote_ai/14_drift_demo.py` | The drift month, and last night's scores against tonight's |
| `tests/quote_ai/test_score.py`, `test_app_season3.py` | The Win model suite's serving checks; the route |

## Deploy and run

```
databricks bundle deploy -t dev                     # see "First deploy" below
databricks bundle run quote_ai_ml_register -t dev
databricks bundle run quote_ai_ml_score -t dev
```

**First deploy in an environment.** The endpoint serves a version of `win_model`, so version 1 must exist before the
bundle can create it: deploy, run `quote_ai_ml_features`, `quote_ai_train` and `quote_ai_ml_register`, then deploy
again. CD passes `--var quote_ai_win_model_version=<the @champion version>` on every deploy, so a deploy never resets
the endpoint to a version written by hand; the register job moves it when it promotes. Smoke test after the dev-shared
deploy: one what-if query must return.

## The checks on every scoring run

- The model is addressed as `models:/<catalog>.<schema>.win_model@champion`; a run ID or a version is refused.
- One non-null prediction per open quote, nothing else (`check_scores`).
- For the same quote the chance never rises with the price (`check_monotonic`; gbt is trained monotonic in price).
- The label `simulation, demo data` is in every answer and on the page.

## The demo's numbers

The draft's "64% at $7.60, 58% at $7.90, 49% at $8.20" is an illustration; the page shows what the champion returns.
Under the planted signal, Lakeside (pays $8.00, Glove A in stock, 7 days) is about 80% at $7.60, 75% at $7.90 and
69% at $8.20; a model trained on the history should land near those and must fall as the price rises.

## Design notes

- Open quotes are the app's live quotes in SHORTLISTED or PRICED (one row per quote: the SKU of its biggest line,
  all its boxes) and the synthetic open quotes of `quote_fact`; `source` says which.
- `open_quote_scores` is appended to (one row per quote per run), so the monitor sees a time series; the draft
  sketch overwrites it. Readers take the latest `scored_at`.
- The sales dashboard tile "open quotes by win chance" is not added: the dashboard lint (AI-10) allows only metric
  views, and the score table is not one. Options: a metric view over `open_quote_scores`, or a lint exception.
- EXECUTE on the model for the app's service principal is granted by the register job; the agent's identity is
  added in AI-18.

## Verify before recording

- Bundle schema for `model_serving_endpoints` (`served_entities`, `ai_gateway.inference_table_config`, `tags`,
  `permissions`) and whether a served entity can follow an alias without a version.
- Feature lookups at serving time for a model logged with Feature Engineering: an online store for
  `account_features` and `sku_features`, and how timestamp keys behave there (latest value).
- `fe.score_batch` with timestamp lookups (`sent_at` = scoring time) and its output columns.
- `serving_endpoints.query(dataframe_records=...)` and the shape of `predictions` for `predict_proba`.
- Data and model quality monitoring (formerly Lakehouse Monitoring): `w.quality_monitors` create, update, refresh,
  `MonitorInferenceLog` fields; `GRANT EXECUTE ON MODEL`.
- Serverless Model Serving with scale to zero in the region; the endpoint's cost in `system.billing.usage`.
- On screen: prices and outcomes are synthetic demo data.
