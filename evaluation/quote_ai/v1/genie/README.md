# Genie suite (AI-10 known answers; AI-11 benchmark)

**Known-answer metric cases (AI-10)** live in `tests/quote_ai/test_metric_definitions.py`: ten quotes, four won, four
lost, two expired, so `MEASURE(win_rate)` is exactly 0.40 at every grain, and a copied definition (0.50) or an average
of averages fails.

**Benchmark (AI-11), `cases.jsonl`:** 40 questions with their expected SQL, 25 on sales (`g-01` to `g-25`) and 15 on
the catalog (`g-26` to `g-40`). Each line: `id`, `section`, `question`, `expected_sql` (with `{sales}`, `{product}`,
`{gudid_gold}`, `{gudid_silver}` filled in for the target), and `compare`: `result` (the rows, in any order) or
`ordered_result` (top-N questions). Cases are compared on results, never on SQL text.

```
python evaluation/quote_ai/run_eval.py --suite genie --catalog dbxarchitectlab_dev --space-id <id> --warehouse-id <id>
```

The score must reach 90% (the release threshold from AI-22). A failed case keeps Genie's SQL to inspect; the fix goes
into the context (comments, ontology, example SQL in `src/quote_ai/semantic/genie/sales_analyst.yml`), never into the
wording of the question. The same questions are loaded into the agent's own benchmarks by `deploy_genie.py`.

**Before recording:** run every expected query once in the SQL editor (`--suite genie` does, as the comparison side)
and check that each returns rows on the synthetic data.
