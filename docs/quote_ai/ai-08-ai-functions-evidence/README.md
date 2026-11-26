# AI-08: AI Functions, glove attributes with evidence

Thousands of GUDID glove descriptions become attributes a rep can compare, each with its evidence. **AI-extracted is
not verified:** a structured GUDID field is verified (source truth), a value an AI Function read from a sentence is an
unverified extracted claim, and two sources that disagree are both marked conflicting for review. Only verified rows
are business truth, and only business truth decides eligibility (AI-15).

| File | What |
| --- | --- |
| `src/quote_ai/pipelines/attributes.py` | Lakeflow pipeline: `ai_extract` (or `ai_query` with a typed schema) over the glove subset, incremental from silver `device_history`; `product_evidence`, `attribute_review` |
| `src/quote_ai/product/evidence.py` | Evidence rows: `normalize` (allowed lists), `gudid_rows`, `ai_rows`, `mark_conflicts`, `evidence_rows`, `invariants`, `to_domain` |
| `src/quote_ai/llm/attributes.py` | The attribute form (one schema for `ai_query` and the chat call), `extract_attributes` |
| `src/quote_ai/llm/prompts.py` | + `extract_glove_attributes` v1 (Prompt Registry, `--name`) |
| `resources/quote_ai/quote_ai_attributes.pipeline.yml` | The pipeline; `quote_ai.method`: `ai_extract` or `ai_query` |
| `evaluation/quote_ai/v1/product_facts/cases.jsonl` | 100 hand-labelled glove descriptions (30 from AI-04) |
| `evaluation/quote_ai/run_eval.py` | `--suite product_facts`: accuracy per attribute, zero AI rows verified, zero structured values overwritten |
| `src/quote_ai/app/backend/routers/evidence.py` | `GET /api/evidence/{sku or product}` |
| `src/quote_ai/app/frontend/src/pages/Product.tsx` | A badge per value: verified (green), unverified (amber), conflicting (red) |
| `tests/quote_ai/test_evidence.py`, `test_product_facts_suite.py` | Precedence, conflicts, allowed lists, unknown writes nothing |

## Precedence

1. Structured GUDID fields (`labeled_contains_nrl`, `labeled_no_nrl`, `device_sterile`, distribution): verified.
2. Supplier documents (AI-09): verified only when all five checks pass.
3. AI extraction from the description: unverified, always (a row that says otherwise cannot be built).

The extraction names map onto the attributes the rules read: `chemo_claim` -> `chemo_tested`, `use_type` ->
`intended_use`, `length_in` -> `length` (unit `in`). `latex_free` is extracted too, so a description that contradicts
the GUDID latex label shows up as a conflict (Glove F in the demo data).

## Deploy and run

```
databricks bundle deploy -t dev
databricks bundle run quote_ai_product_model -t dev      # glove subset, SKUs (input)
databricks bundle run quote_ai_attributes -t dev
python evaluation/quote_ai/run_eval.py --suite product_facts --target dev
python -m quote_ai.llm.prompts --catalog dbxarchitectlab_dev --name extract_glove_attributes --alias production
```

Cost per 1,000 devices: tokens from the gateway usage table for `quote_ai_extract_<target>` and DBUs from
`system.billing.usage` for the pipeline's run, divided by the rows in `ai_glove_attributes`
(`quote_ai.llm.compare_models.cost_per_1000` does the arithmetic).

## Verify before recording

- `ai_extract` signature and output (a struct of strings named by the labels); `ai_query(..., responseFormat => ...)`;
  both inside a streaming table of a serverless pipeline.
- The GUDID silver column names above, and that `device_history` change commits are skipped (incremental reruns).
- The 100 cases are GUDID-style stand-ins (`"di": null`): replace them with real records, labelled by hand.
- The demo descriptions of the tracer gloves in the app's local demo (`quote_ai.storage.evidence_store.DEMO_DEVICES`)
  are synthetic; in the workspace, Gloves A to F are bound to real GUDID devices whose descriptions differ.
