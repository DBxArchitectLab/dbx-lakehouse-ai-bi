# 0005: Optimization: work the levers in order, no fine-tuning now

- Status: proposed (AI-23). The numbers marked *local* come from the stand-in run in the repo (no model service);
  replace the *dev* column with the runs of the `quote_ai_experiment` job on recording day and accept or change the
  decision.
- Episode: AI-23.

## Context

The baseline scorecard for the Lakeside request and the full package (evaluation package 1.1.0, with the held-out
slice of messy search requests): eligibility, pricing and the workflow gates are perfect, because they are rules. Two
areas are weak:

- **Search recall on messy requests.** The held-out slice (S-H01 to S-H12: typos, no catalog words) is well below the
  rest of the search suite.
- **Comparison cost and latency.** A large model writing long explanations: most of the cost per quote and most of
  the wait on the Compare screen (about 14 seconds and almost a cent per quote in dev, before this record).

The tempting move is to fine-tune a model for extraction and comparison. It is expensive, slow to change, and it
learns facts (stock, evidence, prices) that are stale tomorrow.

## Options

- **(a)** Fine-tune now, for extraction and comparison.
- **(b)** Work the levers in order, cheapest and most reversible first, one variable per run, and stop when the
  targets are met. (Chosen.)

## The rule, written before any run

A variant ships only if every required suite still meets its threshold (the AI-22 gate, `v1/thresholds.yml`) **and**
the cost per quote or the p95 latency improves. A quality lever (E2, retrieval) ships when its target score improves
at no extra cost and no slower. Cheaper is only better if the gate still passes. Code:
`evaluation/quote_ai/experiments/run_experiment.py` (`ship`, `needs_tuning`), tested in
`tests/quote_ai/test_optimize.py`.

```
databricks bundle run quote_ai_experiment -t dev --params name=baseline
databricks bundle run quote_ai_experiment -t dev --params name=E1,variable=comparison_prompt,value=v4
databricks bundle run quote_ai_experiment -t dev --params name=E2,variable=retriever,value=hybrid_weighted_rerank
databricks bundle run quote_ai_experiment -t dev --params name=E3,variable=comparison_model,value=quote_ai_chat_small
databricks bundle run quote_ai_experiment -t dev --params name=E4,variable=cache,value=on
```

## Results

| Run | One variable | Gate | Cost per quote (local) | Held-out Recall@5 (local) | Dev (recording day) | Ships? |
| --- | --- | --- | --- | --- | --- | --- |
| baseline | - | passes | $0.00112 | 0.75 | cost, p95, grounded: to fill | - |
| E1 | compare_explain v4 (shorter, table first) | passes; layer_cited 1.0 | $0.00104 | 0.75 | to fill | yes (cost) |
| E2 | retriever `hybrid_weighted_rerank` (keyword weight 0.1, reranker) | passes | $0.00112 | 0.94 (search suite 0.91 -> 0.96) | to fill | yes (recall at no extra cost) |
| E3 | comparison on `quote_ai_chat_small` | needs dev: grounded_rate is a judge | $0.00052 (estimate at list prices) | 0.75 | grounded >= 0.90? Glove B still unknown? | only if the dev gate passes |
| E3b | comparison on the smallest model | needs dev | lower again | 0.75 | expected to fail grounded (< 0.90) | no, if the gate is red |
| E4 | cache on extraction (unchanged GUDID records) | passes | $0.00080 (second run: extraction 0) | 0.75 | to fill | yes (cost) |
| E5 | tuning | - | - | - | only if E1-E4 leave a gap | no |

Local numbers: the comparison answers come from the extractive stand-in, so quality there is the deterministic part
(truth layers, account filter, no evidence); the cost is the prompt and answer size at the list prices in
`config/quote_ai/models.yml`. A model change measured on the stand-in never ships (`quality_measured: false`).

## Decision

- Promote **E1** (the champion alias of `compare_explain` moves to v4), **E2** (the retriever variant
  `hybrid_weighted_rerank`) and **E4** (the cache on extraction), each in a pull request that passes the
  `quote_ai_eval` gate.
- **E3**: promote `quote_ai_chat_small` for the comparison (one line in `config/quote_ai/models.yml`) only if its dev
  run keeps grounded >= 0.90, every statement citing its layer, eligibility at zero violations and Glove B unknown.
  **E3b** is not promoted when its comparison suite is red.
- **E5: no fine-tuning now.** E1-E4 leave no required suite below its threshold (`needs_tuning`).

## Revisit

- A new foundation model in the region, or a price change: re-run E3.
- Volume: a tenfold increase in quotes per day: re-run E4 (cache hit rate) and the cost per quote.
- New device types or a new supplier catalog: re-run E2 on a refreshed held-out slice.
- Any required suite falling below its threshold after E1-E4: E5, with its own decision record.
