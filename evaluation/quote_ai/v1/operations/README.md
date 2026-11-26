# Operations suite (declared in AI-22, required from AI-26)

The operating rules of QuoteAI in production, run on the operations fixtures (`quote_ai.fixtures.operations`) by
`evaluation/quote_ai/operations.py`, in CI and in the eval job. Zero tolerance on every metric:

| Case | Kind | What must never happen |
| --- | --- | --- |
| ops-01 | distribution_change | Glove D leaves distribution in today's GUDID update and is still offered after the daily run |
| ops-02 | report_arrives | Glove B not eligible on the first run after its verified chemo report (the eligibility cases E-014, without the report, and E-015, with it, each keep passing on their own snapshot) |
| ops-03 | stale_issue | A quote issued while the daily job has been paused 30 hours (the stock is older than the policy) |
| ops-04 to 06 | freshness | The freshness SQL naming the wrong sources past their limit (paused job; normal morning; a document waiting) |
| ops-07 | cost | A quote without every step in its cost, or a day's compute not spread exactly over its quotes |
| ops-08 | live_scoring | The gate's scorers scoring live traces differently from the suites (unknown counted as a match) |

Production's own numbers (freshness now, p95 latency, live grounded rate, cost per quote) are watched on the Ops page
and the operating dashboard (`quote_ai_monitoring`), not gated: a release can't make yesterday's data younger.
