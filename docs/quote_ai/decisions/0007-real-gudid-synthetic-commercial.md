# 0007: Real GUDID, synthetic commercial data; the win model is a simulation

- Status: accepted (written in AI-27, capturing the choice made in AI-01 to AI-03).
- Episode: AI-03 (the product model), AI-12 to AI-14 (the win model).

## Context

QuoteAI needs product facts that are real enough to be worth verifying, and commercial data (prices, stock, customers,
quotes, supplier documents) that no public source can give. Real sales data is confidential; invented device data
would make every evidence and freshness lesson meaningless.

## Options

- **(a)** Everything synthetic: easy, but nothing to verify and no real daily change.
- **(b)** Real device data from the FDA's AccessGUDID (public, daily updates, a v3 API), and synthetic DBxLab
  commercial data, labelled as demo data everywhere it appears. (Chosen.)
- **(c)** Real commercial data from a partner: not publishable, not reproducible by viewers.

## Decision

(b). GUDID is ingested by the AI-02 framework (`config/quote_ai/ingest/sources.yml`) under its terms
(`docs/quote_ai/data-sources.md`). Price book, stock, customers, quote history, supplier documents and their test
reports are generated (`src/quote_ai/mock/`), deterministic and labelled "demo data" in the app, the dashboards and
the Ops page. The win model is trained on synthetic history with a planted signal: it is an **educational simulation**
that shows the method (point-in-time features, a baseline written first, promotion by alias), not evidence of real
sales effectiveness.

## Consequences

- Every screen and suite says which numbers are demo data; the capstone scorecard calls the win model a simulation.
- Real brands are pseudonymized wherever synthetic quality or price appears next to them.
- Freshness, distribution changes and record versions are real (GUDID); a supplier report "arriving" is a scenario
  (`quote_ai.mock.documents`, `quote_ai.fixtures.operations.report_arrives`).
- Not for clinical decisions, not customer-facing: sales quoting support only.

## Tests

`tests/quote_ai/test_full_release_selection.py`, the ingestion suite; `test_train.py` (the planted signal is
recovered, the baseline is beaten on held-out synthetic months).
