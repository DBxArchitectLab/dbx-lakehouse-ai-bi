-- SQL alerts on the metric views (AI-10). Bundle support for alerts is to be verified; until then each query is saved
-- as a SQL alert by hand (docs/quote_ai/ai-10-metric-views-dashboards/README.md), with the condition in its comment.
-- {sales} is catalog.schema from the bundle; render with quote_ai.storage.sql_files before pasting.

-- Alert 1: average discount on nitrile above 12% this month. Condition: avg_discount_pct > 0.12
SELECT MEASURE(avg_discount_pct) AS avg_discount_pct
FROM {sales}.quote_metrics
WHERE material = 'nitrile' AND month = date_trunc('MONTH', current_date());

-- Alert 2: quote turnaround slower than a working day this month. Condition: quote_turnaround_h > 24
SELECT MEASURE(quote_turnaround_h) AS quote_turnaround_h
FROM {sales}.quote_metrics
WHERE month = date_trunc('MONTH', current_date());
