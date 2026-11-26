-- Quote metrics (AI-10): DBxLab's sales measures, defined once, owned by Sales Ops. Every consumer (the AI/BI
-- dashboard, the SQL editor, Genie in AI-11, the agent later) queries them with MEASURE(); nobody re-implements one.
-- Win rate: won / decided, where decided = won + lost + expired. Expired counts as lost; cancelled is excluded
-- (docs/quote_ai/decisions/0003-win-rate-definition.md; the same definition is the win model's label in AI-12).
--   SELECT region, MEASURE(win_rate) FROM {sales}.quote_metrics GROUP BY region
-- {sales} is catalog.schema from the bundle. Run by src/quote_ai/jobs/semantic_job.py metrics.
-- Verify before recording: the metric view YAML version (1.1 for comments), and COMMENT before AS.

CREATE OR REPLACE VIEW {sales}.quote_metrics
WITH METRICS
LANGUAGE YAML
COMMENT 'DBxLab quote metrics (demo data), owned by Sales Ops: quotes, win rate, discount, turnaround, approvals. Query with MEASURE(); expired counts as lost, cancelled is excluded.'
AS $$
version: 1.1
source: {sales}.quote_fact
comment: One row per quote. Measures are computed at query time, so ratios are right at every grain.
dimensions:
  - name: region
    expr: region
    comment: Sales region of the account
  - name: rep
    expr: rep_id
    comment: The rep who owns the quote
  - name: account
    expr: account_name
    comment: Customer account name, e.g. Lakeside Regional Clinic
  - name: product_family
    expr: product_family
    comment: DBxLab product family, e.g. GLOVE-A
  - name: material
    expr: material
    comment: Glove material, e.g. nitrile
  - name: month
    expr: date_trunc('MONTH', requested_at)
    comment: The month the request was received
  - name: state
    expr: state
    comment: Quote state
measures:
  - name: quotes
    expr: COUNT_IF(state <> 'CANCELLED')
    comment: Number of quotes, excluding cancelled ones
  - name: win_rate
    expr: COUNT_IF(outcome = 'WON') / NULLIF(COUNT_IF(outcome IN ('WON', 'LOST', 'EXPIRED')), 0)
    comment: Won over decided quotes. Expired counts as lost; cancelled is excluded (decision record 0003)
  - name: avg_discount_pct
    expr: SUM((list_price - unit_price) * quantity) FILTER (WHERE state <> 'CANCELLED') / NULLIF(SUM(list_price * quantity) FILTER (WHERE state <> 'CANCELLED'), 0)
    comment: Discount against list price, weighted by revenue at list price
  - name: quote_turnaround_h
    expr: MEDIAN((unix_timestamp(issued_at) - unix_timestamp(requested_at)) / 3600)
    comment: Median hours from the request being received to the quote being issued
  - name: approval_rate
    expr: COUNT_IF(approval_needed AND issued_at IS NOT NULL) / NULLIF(COUNT_IF(issued_at IS NOT NULL), 0)
    comment: Quotes that needed a manager's approval over quotes issued
$$
