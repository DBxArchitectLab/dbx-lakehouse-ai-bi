# AI-17: Search, eligibility and RAG quality

Measure before improving. Three suites with separate thresholds, never one blended score: **Eligibility** is a hard
gate (zero violations, every contract requirement covered, every case pinned to an evidence snapshot), **Search**
and **Comparison** are metrics that must not drop, plus their own zero-tolerance gates (eligibility violations,
truth layers, cross-account documents, "no evidence on file").

| File | What |
| --- | --- |
| `evaluation/quote_ai/v1/search/cases.jsonl` | 50 requests, expected products, must-not-include, account |
| `evaluation/quote_ai/v1/eligibility/cases.jsonl` | 60 cases (grown from 20), traps, E-014 / E-015 for Glove B |
| `evaluation/quote_ai/v1/comparison/cases.jsonl` | 30 questions (grown from 20), permission cases |
| `evaluation/quote_ai/scorers.py` | recall_at_5, eligibility_violations, eligibility_case_failures, truth_layer_violations, citation_accuracy, source_recall, cross_account_violations, no_evidence_failures; `mlflow_scorers` |
| `evaluation/quote_ai/season4.py` | Predict functions per suite, the variants, local runs and `mlflow.genai.evaluate` runs |
| `evaluation/quote_ai/run_eval.py` | `--suite search / eligibility / fulfilment / comparison`, `--variant`, `--mlflow` |
| `src/quote_ai/search/retriever.py` | `RetrieverConfig`, `VARIANTS` (baseline, hybrid, hybrid_section, hybrid_rerank), reranking, account filter |
| `src/quote_ai/storage/accounts.py`, `app/backend/routers/compare.py` | The signed-in rep's accounts filter retrieval before it runs |
| `src/quote_ai/jobs/eval_job.py`, `resources/quote_ai/quote_ai_eval_tables.job.yml`, `evaluation/quote_ai/eval_tables.sql` | The cases as tables in `quote_ai_eval`; experiment `/Shared/quote_ai/eval` |
| `.github/workflows/ci.yml` | + the Eligibility and Fulfilment suites on every pull request |
| `tests/quote_ai/test_scorers.py` | The scorers on toy data; the suites as files and as runs; the breaks turn red |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_eval_tables
python evaluation/quote_ai/run_eval.py --suite search --variant baseline --mlflow
python evaluation/quote_ai/run_eval.py --suite search --variant hybrid_rerank --mlflow
python evaluation/quote_ai/run_eval.py --suite eligibility
python evaluation/quote_ai/run_eval.py --suite comparison --variant build --endpoint quote_ai_chat_dev --mlflow
```

## Results recorded so far (local stand-in index, no workspace)

These come from `quote_ai.search.local` (a deterministic stand-in for embeddings), so they test the suites and the
gates, not AI Search. The episode's numbers (baseline about 0.70, hybrid_section 0.86) must come from the workspace
runs; record them here.

| Suite | Variant | Metrics | Gates |
| --- | --- | --- | --- |
| search | baseline | recall@5 0.960 | eligibility_violations 0 |
| search | hybrid | recall@5 0.948 | 0 |
| search | hybrid_section | recall@5 0.948 | 0 |
| search | hybrid_rerank | recall@5 0.967 | 0 |
| search | leaky_summary (must fail) | recall@5 0.948 | **11** (Glove B eligible from its description, among others) |
| eligibility | - | - | case failures 0, violations 0 |
| eligibility | relax_latex (the demo's break) | - | **red** |
| comparison | baseline (fixed500, vector) | citation accuracy 1.00, source recall 0.60 | 0, 0, 0 |
| comparison | hybrid_section (chosen) | citation accuracy 1.00, source recall 0.97 | 0, 0, 0 |

Locally the stand-in "vector" search already reads concepts well, so hybrid doesn't beat it on these requests; on
real embeddings the catalog-number requests (S-042 to S-050) are where hybrid should win. The extractive generator
only quotes, so its citation accuracy is 1.0 by construction; the model's will not be.

## Permission test

rep.bob (rep_north: Harbor Point, Summit) asks about Lakeside's price sheet: nothing from ACC-001 is retrieved, no
citation, "no evidence on file". rep.alice (rep_south: Lakeside) gets it, cited. The accounts come from the identity
Databricks Apps forwards (`X-Forwarded-Email`), looked up in `quote_ai_sales.rep_account`, and filter the AI Search
query itself. Until AI-24 this is QuoteAI's code (the app queries as its service principal); AI-24 adds Unity
Catalog user authorization and row filters.

## Differences from the draft

- The draft's `rep_north` / `rep_south` and `ACC-LAKESIDE` are `rep.bob` / `rep.alice` and `ACC-001` (AI-02's
  synthetic customers).
- Search expected SKUs are one SKU per product (the first size asked); Recall@5 counts products in rank order.
- The eligibility suite also counts per-requirement differences (`eligibility_case_failures`), not only safety
  violations, both at zero.

## Verify in the workspace

`mlflow.genai.evaluate` data format and metric names; `@scorer`; the built-in groundedness judge; the review app
(labeling sessions) on Azure; AI Search reranking and hybrid weighting; the identity headers Databricks Apps forwards.
