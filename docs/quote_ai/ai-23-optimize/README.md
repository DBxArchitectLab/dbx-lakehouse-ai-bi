# AI-23: Optimize quality, cost and latency

Now that the gate measures, we improve: prompts first, then retrieval, then models, and fine-tuning only if the
numbers say so. Five experiments, one variable each, all against evaluation package 1.1.0; the rule and the results
are decision record 0005. No new screen: the same Compare view, faster and cheaper.

| File | What |
| --- | --- |
| `src/quote_ai/llm/prompts.py` | `CHAMPION` / `CHALLENGER`, `load_prompt(name, alias)` (a missing alias raises `PromptAliasMissing`, never a fallback), `compare_explain` v4 (E1: shorter, table first) |
| `src/quote_ai/llm/client.py` | `model_for(use)`: the model service and token limit per use from `config/quote_ai/models.yml`; `cost_usd` |
| `config/quote_ai/models.yml` | extraction, comparison, agent, embedding: one service each, with `max_tokens`; list prices for the cost per quote |
| `src/quote_ai/search/retriever.py`, `search/local.py` | `hybrid_weight` on hybrid queries (`hybrid_kwargs`), the E2 variant `hybrid_weighted_rerank` (20 candidates, reranked) |
| `src/quote_ai/llm/cache.py`, `llm/llm_cache.sql` | `cache_key(kind, payload, version)`, `version_of(prompt, model, publicVersionNumber)`, `LlmCache`; price, stock and account calls never cached |
| `src/quote_ai/llm/attributes.py` | `extract_attributes(..., cache=, public_version_number=)`: an unchanged GUDID record is read once (E4) |
| `src/quote_ai/app/backend/context.py`, `app/app.yaml` | Every prompt read by the `champion` alias; the comparison's `max_tokens` from the config |
| `evaluation/quote_ai/experiments/run_experiment.py` | One variable per run (`comparison_prompt`, `retriever`, `comparison_model`, `cache`; `tuning` is a decision), scores + `cost_per_quote` + `p95_latency_s`, `ship`, `needs_tuning`, MLflow logging |
| `evaluation/quote_ai/v1/search/cases.jsonl`, `v1/VERSION` | 1.1.0: the held-out slice S-H01 to S-H12 (messy requests, `"split": "heldout"`) |
| `resources/quote_ai/quote_ai_experiment.job.yml` | The `quote_ai_experiment` job (dev only) and the `/Shared/quote_ai/experiments` experiment |
| `resources/quote_ai/quote_ai_chat_small.model_service.yml`, `databricks.yml` | E3's smaller comparison model, created by the bundle (`quote_ai_chat_small_model`) |
| `scripts/quote_ai/build_app.py` | The app's vendored package carries `models.yml` |
| `docs/quote_ai/decisions/0005-optimization.md` | Context, options, the rule, results per experiment, decision, when to revisit |
| `tests/quote_ai/test_cache.py`, `test_optimize.py` | The cache, the alias error, models by config, the hybrid weight; the ship rule (E3 passes, E3b fails), E4, E5 |

## Deploy and run

```
databricks bundle deploy -t dev
python -m quote_ai.llm.prompts --catalog <catalog> --name compare_explain --version v1 --alias champion
python -m quote_ai.llm.prompts --catalog <catalog> --name extract_requirements --version v4 --alias champion
databricks bundle run quote_ai_experiment -t dev --params name=E1,variable=comparison_prompt,value=v4
databricks bundle run quote_ai_eval -t dev
python evaluation/quote_ai/experiments/run_experiment.py --name E2 --variable retriever \
    --value hybrid_weighted_rerank --compare-to baseline.json          # locally
```

Every prompt now needs a `champion` alias before the app starts (AI-05 and AI-16 registered `production`): set it
for `extract_requirements` (v4) and `compare_explain` (v1, or v4 once E1 is promoted).

## Verify before recording

- Prompt Registry: `mlflow.genai.load_prompt` on a missing alias (the error it raises), aliases in Unity Catalog.
- MLflow prompt optimization availability (not used in code; the slide only).
- Databricks AI Search: whether a HYBRID query takes a keyword/vector weight and its parameter name
  (`retriever.hybrid_kwargs`; only the local index takes it today); the reranker parameters.
- The foundation models in the region and their prices (`quote_ai_chat_small_model`, `models.yml` prices).
- Current fine-tuning options on Azure Databricks (E5 is a decision, not code).

## Differences from the draft

- The draft names the aliases champion/challenger; AI-05 to AI-22 used `production`. The script follows the draft, so
  the code moved to `champion` (app.yaml, `run_eval --alias`, `extract.prompt_uri`).
- "comparison prompt v4": `compare_explain` had only v1; the new version is registered as v4 to match the script.
- E3's quality cannot be measured without the model service: locally the runner marks it `quality_measured: false`
  and `ship` refuses it; the decision record has placeholders for the dev numbers (grounded 0.92 / 0.84 in the script).
- Locally the gain of E2 comes from the hybrid weight toward meaning (0.1) with the reranker; the dev run decides
  the final weight. The search suite's own threshold is unchanged (Recall@5 >= 0.85 over all 62 cases).
- Promoting E2 is a one-line pull request (`retriever.CHOSEN`, and the shortlist's `rerank`), not done here: the
  decision record is proposed until the dev runs.
