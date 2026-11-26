-- Cost per quote (AI-26), split by step: you can only reduce what you can see. Two sources:
--   model calls    the usage recorded per trace (quote ID, step, model service, tokens), priced with the list prices
--                  in config/quote_ai/models.yml (model_prices, written by the quote_ai_monitors setup task)
--   compute        the billing system tables per day, tagged app: quote_ai: the app's compute (it bills while the app
--                  runs, used or not), the win model's serving and the jobs; each day's cost is spread over that
--                  day's quotes, as its own step
-- The Ops page, the operating dashboard and the cost alert read cost_per_quote. {monitoring}: catalog.schema from the
-- bundle. Statements that read system.* tables run in the workspace only; the rest is unit-tested on fixtures on
-- SQLite (tests/quote_ai/test_operations.py, quote_ai.ops.monitoring.run_local).
-- Monitoring holds no PII: no rep, no customer contact, no request text; sampled traces are masked (AI-24).

CREATE TABLE IF NOT EXISTS {monitoring}.trace_usage (
  day               DATE   COMMENT 'The day of the call (UTC)',
  quote_id          STRING COMMENT 'The quote the call served (from the trace tags)',
  trace_id          STRING COMMENT 'The MLflow trace',
  step              STRING COMMENT 'extraction, retrieval, comparison, agent, issue',
  service           STRING COMMENT 'The model service, without the target suffix (quote_ai_chat)',
  prompt_tokens     BIGINT COMMENT 'Input tokens',
  completion_tokens BIGINT COMMENT 'Output tokens',
  latency_ms        BIGINT COMMENT 'Latency of the step',
  status            STRING COMMENT 'ok, error, or stale_refused (QuoteService refused ISSUED on stale inputs)'
) COMMENT 'QuoteAI usage per trace and step (AI-26): written daily by quote_ai_live_scoring from the production traces. No PII.';

CREATE TABLE IF NOT EXISTS {monitoring}.model_prices (
  service      STRING COMMENT 'The model service (quote_ai_extract, quote_ai_chat, ...)',
  input_per_1k DOUBLE COMMENT 'USD per 1,000 input tokens (config/quote_ai/models.yml)',
  output_per_1k DOUBLE COMMENT 'USD per 1,000 output tokens'
) COMMENT 'QuoteAI model list prices (AI-26), from config/quote_ai/models.yml: one source of truth with the experiments (AI-23).';

CREATE TABLE IF NOT EXISTS {monitoring}.daily_compute (
  day      DATE   COMMENT 'The usage date',
  step     STRING COMMENT 'app, serving or compute',
  cost_usd DOUBLE COMMENT 'List-price cost of the day',
  source   STRING COMMENT 'Where it came from (system.billing)'
) COMMENT 'QuoteAI compute cost per day and step (AI-26), from the billing system tables (tag app: quote_ai).';

-- Workspace only: the last seven days of compute, from the billing system tables (re-run safe: the window is replaced).
-- Verify before recording: system.billing.usage columns (usage_date, billing_origin_product, custom_tags,
-- usage_quantity, sku_name) and list_prices (pricing.default, price_start_time, price_end_time); whether the app and
-- the serving endpoint carry the custom tag app = quote_ai; and that pay-per-token model calls are NOT in these
-- products (they are priced from tokens above, so counting them here would count them twice).
DELETE FROM {monitoring}.daily_compute WHERE day >= date_sub(current_date(), 7);

INSERT INTO {monitoring}.daily_compute
SELECT u.usage_date AS day,
       CASE u.billing_origin_product WHEN 'APPS' THEN 'app' WHEN 'MODEL_SERVING' THEN 'serving' ELSE 'compute' END AS step,
       sum(u.usage_quantity * p.pricing.default) AS cost_usd,
       'system.billing' AS source
FROM system.billing.usage u
JOIN system.billing.list_prices p
  ON p.sku_name = u.sku_name AND u.usage_start_time >= p.price_start_time
 AND (p.price_end_time IS NULL OR u.usage_start_time < p.price_end_time)
WHERE u.custom_tags['app'] = 'quote_ai'
  AND u.billing_origin_product IN ('APPS', 'MODEL_SERVING', 'JOBS', 'DLT', 'SQL', 'VECTOR_SEARCH')
  AND u.usage_date >= date_sub(current_date(), 7)
GROUP BY u.usage_date,
         CASE u.billing_origin_product WHEN 'APPS' THEN 'app' WHEN 'MODEL_SERVING' THEN 'serving' ELSE 'compute' END;

CREATE OR REPLACE VIEW {monitoring}.cost_per_quote
COMMENT 'QuoteAI cost per quote and step (AI-26): model calls from the trace usage at the models.yml prices, plus each day''s app, serving and job compute spread over that day''s quotes.'
AS
WITH model_cost AS (
  SELECT u.day, u.quote_id, u.step,
         sum(u.prompt_tokens * p.input_per_1k + u.completion_tokens * p.output_per_1k) / 1000.0 AS cost_usd
  FROM {monitoring}.trace_usage u
  JOIN {monitoring}.model_prices p ON p.service = u.service
  GROUP BY u.day, u.quote_id, u.step
),
day_quotes AS (
  SELECT DISTINCT day, quote_id FROM {monitoring}.trace_usage
),
quotes_per_day AS (
  SELECT day, count(*) AS quotes FROM day_quotes GROUP BY day
),
spread AS (
  -- compute bills whether or not anyone quotes: each day's cost over that day's quotes, as its own step
  SELECT c.day, d.quote_id, c.step, c.cost_usd / q.quotes AS cost_usd
  FROM {monitoring}.daily_compute c
  JOIN quotes_per_day q ON q.day = c.day
  JOIN day_quotes d ON d.day = c.day
)
SELECT day, quote_id, step, cost_usd FROM model_cost
UNION ALL
SELECT day, quote_id, step, cost_usd FROM spread;
