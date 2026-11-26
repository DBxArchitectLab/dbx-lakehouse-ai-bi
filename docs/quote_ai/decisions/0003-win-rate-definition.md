# 0003: Win rate is won over decided quotes; expired counts as lost, cancelled is excluded

- **Episode:** AI-10, metric views and AI/BI dashboards
- **Status:** Accepted
- **Owner:** Sales Ops

## Context

Three ad hoc queries for "win rate on nitrile exam gloves" gave 41%, 38% and 46%. One counted expired quotes as lost,
one also counted cancelled quotes in the denominator, one left expired quotes out. The dashboard, Genie (AI-11) and
the win model (AI-12) all need one answer.

## Decision 1: the definition

`win_rate = COUNT_IF(outcome = 'WON') / NULLIF(COUNT_IF(outcome IN ('WON', 'LOST', 'EXPIRED')), 0)`

- **Expired counts as lost.** A quote the customer let expire is a quote we did not win; leaving it out flatters the
  rate (0.50 instead of 0.40 on the known-answer fixture).
- **Cancelled is excluded.** A cancelled quote was withdrawn before the customer decided (by us or by the customer
  before pricing); it says nothing about winning.
- **Open quotes are excluded** until they are decided.
- The ratio is computed at query time from the rows: win rate by region is never the average of the reps' rates.

## Decision 2: where the definition lives

Options: (a) in each dashboard dataset; (b) summary tables refreshed by a job; (c) a metric view in Unity Catalog.
**Choice: (c)** `quote_ai_sales.quote_metrics` (`src/quote_ai/semantic/quote_metrics.sql`). One definition, right at
every grain, with an owner, comments and permissions; the dashboard, the SQL editor, Genie and the agent query it
with `MEASURE(win_rate)`.

## Consequences

- AI-12 uses the same definition for the win model's label: an expired quote is a negative example.
- `tests/quote_ai/test_metric_definitions.py` runs the measure expression from the file on a fixture of 10 quotes
  (4 won, 4 lost, 2 expired) and expects exactly 0.40; a dataset that writes its own win rate fails the dashboard lint.
- Changing the definition is a pull request on this record and the metric view, never a dashboard edit.
