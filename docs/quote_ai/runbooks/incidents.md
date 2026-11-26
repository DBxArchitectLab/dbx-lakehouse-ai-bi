# Runbook: QuoteAI incidents (AI-26)

Owner: the QuoteAI platform team (DBX_Architect_Lab_Admin). Alerts route to the owner; the operating dashboard
("QuoteAI - Operations") is shared with the platform team and DBxLab_Sales_Managers, who also see the same numbers on
the app's Ops page. The targets are in `config/quote_ai/slo.yml`; the limits that refuse an issue are in
`config/quote_ai/policy.yml`. Every table below is in `quote_ai_monitoring`.

| Symptom | First check | Action |
| --- | --- | --- |
| Freshness alert (`quote_ai_freshness`: a watched source over 24 h) | The `quote_ai_gudid_daily` run and the file date in `quote_ai_ops.ingestion_log` / `ingestion_file` | Re-run the job: `databricks bundle run quote_ai_gudid_daily -t prod` (a replay is a no-op, AI-02) |
| Issue refused: stale inputs (the refusal names the input and its date) | `quote_ai_monitoring.freshness` (kind = input): which source is late | Re-run that source: the daily job for GUDID and evidence; `quote_ai_ingest_on_demand` for stock and the price book |
| Eligibility "unknown" spike (`slo_daily.unknown_share`) | The evidence pipelines (`quote_ai_attributes`, `quote_ai_documents`), documents waiting for QA sign-off (freshness: supplier_documents) | Re-extract: run the pipeline; ask QA to sign off the waiting documents |
| Grounded rate drop (`slo_daily.grounded_rate` under 0.90) | A recent prompt or model change: `release/release.yml`, `config/quote_ai/models.yml`, the prompt aliases | Roll back the alias: `docs/quote_ai/runbooks/release-and-rollback.md` (aliases only) |
| Latency p95 breach (`slo_daily.p95_latency_s` over 20 s) | Model service metrics, rate limits and gateway usage per service | Scale, raise the limit in the bundle, or fall back to the smaller model (decision record 0005) |
| Cost spike (`quote_ai_cost_per_quote` alert) | `cost_per_quote` by step, and usage by user in the gateway usage | Rate limit the service (Unity Gateway); check the app's compute and the jobs' schedules |
| App errors or slow screens | Apps > quote-ai-prod > Logs; the app's status | Roll back the app version: the AI-25 rollback (`rollback_to` the previous tag) |
| Comparison endpoint down | App logs: "comparison model call failed"; the Compare screen shows the table without prose | Nothing is wrong with a quote: the table and the rules still answer. Check the chat model service; escalate to the platform team |

## Rules that hold during any incident

- A stale or fallback copy may inform the rep; it never issues a quote (QuoteService refuses ISSUED and says why).
- Nobody fixes prod by hand: every fix is a re-run of a bundle job or a pull request through the pipeline.
- Monitoring holds no PII: scores, counts and costs only. Sampled traces are read by the job's identity and masked.

## After the incident

Write what happened, the first sign, the fix and the follow-up in the pull request that fixes it; if a test could have
caught it, add the case to the evaluation package (`evaluation/quote_ai/v1/`), so the gate catches it next time.
