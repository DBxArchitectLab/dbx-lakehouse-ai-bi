-- The monitoring tables of live quality and the daily SLOs (AI-26), in quote_ai_monitoring next to the win model's
-- inference monitor (AI-14). Written by the quote_ai_live_scoring job; read by the Ops page and the operating
-- dashboard. {monitoring}: catalog.schema from the bundle. No PII: scores and counts only.

CREATE TABLE IF NOT EXISTS {monitoring}.live_scores (
  day      DATE   COMMENT 'The day of the sampled traces',
  scorer   STRING COMMENT 'The gate scorer (grounded_rate, eligibility_violations, unknown_as_match, unknown_share, layer_cited)',
  value    DOUBLE COMMENT 'Its value on the sample',
  n        BIGINT COMMENT 'Sampled traces scored',
  scored_at TIMESTAMP COMMENT 'When the job scored them'
) COMMENT 'QuoteAI live quality (AI-26): the same scorers as the evaluation gate (AI-22) on a daily sample of production traces.';

CREATE TABLE IF NOT EXISTS {monitoring}.slo_daily (
  day                    DATE    COMMENT 'The day',
  quotes                 BIGINT  COMMENT 'Quotes with traced activity',
  p95_latency_s          DOUBLE  COMMENT '95th percentile of a quote''s traced steps, seconds',
  under_target_share     DOUBLE  COMMENT 'Share of quotes under the latency target (slo.yml)',
  errors                 BIGINT  COMMENT 'Failed steps',
  stale_refusals         BIGINT  COMMENT 'Issues refused because an input was older than the policy allows',
  grounded_rate          DOUBLE  COMMENT 'Live: the grounded_comparison judge',
  eligibility_violations DOUBLE  COMMENT 'Live: ineligible gloves offered or unknown counted as a match',
  unknown_share          DOUBLE  COMMENT 'Live: share of eligibility checks that are unknown',
  cost_per_quote_usd     DOUBLE  COMMENT 'Average cost per quote, every step (cost_per_quote)',
  ok                     BOOLEAN COMMENT 'Every SLO of config/quote_ai/slo.yml met that day'
) COMMENT 'QuoteAI service level objectives per day (AI-26): what the Ops page and the operating dashboard show.';
