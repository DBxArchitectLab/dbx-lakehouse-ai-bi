# Fulfilment suite (AI-15)

Zero tolerance: each fixture returns its expected answer (full, partial, backorder, alternative date), and a line
past the hard delivery date is never on time (`late: true` means "alternative, needs acceptance").

One line per case: `case_id`, `note`, `quantity`, `stock` (a `quote_ai.rules.fulfilment.Stock` without `sku_id`),
`delivery_days`, `expected: {status, late}`. Checked by `tests/quote_ai/test_fulfilment.py` and
`python evaluation/quote_ai/run_eval.py --suite fulfilment`.
