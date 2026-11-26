# 0001: A model per job, chosen by evidence

- **Episode:** AI-04, LLM fundamentals
- **Status:** Proposed. Confirm with the numbers from the `04_model_comparison` run before recording, then mark
  Accepted with the run date.

## Context

QuoteAI calls a model for different jobs: reading glove descriptions and requests into fields (extraction), talking
to a rep (chat), and turning text into vectors (embeddings, AI-15). One model for everything is either too expensive
for the bulk job or too weak for the conversation. Every later model call rests on this choice.

## How it was measured

`notebooks/quote_ai/04_model_comparison.py` runs `quote_ai.llm.compare_models` on the 30 hand-labelled gloves in
`evaluation/quote_ai/v1/product_facts/`, three pay-per-token Foundation Model API endpoints, temperature 0, the same
prompt. One MLflow run per endpoint.

| Endpoint | Field accuracy | Hallucinations | Tokens in / out | Avg latency | USD per 1,000 gloves |
| --- | --- | --- | --- | --- | --- |
| (from the run) | | | | | |

"Not stated" (null) is a correct answer; a value the text does not support counts as a hallucination.

## Decision

- **Extraction** (glove descriptions now, requests in AI-05, AI Functions in AI-08): the cheapest endpoint whose field
  accuracy is within 2 points of the best, with zero hallucinations on the chemo probe, temperature 0, JSON schema
  output.
- **Chat** (the rep-facing assistant, AI-16 onward): the most accurate endpoint, where latency per turn is acceptable;
  cost matters less at a rep's volume.
- **Embeddings** (AI-15): decided there, on the Search suite.

Endpoint names live in configuration, never in code: `quote_ai.llm.endpoints` from AI-06, one Unity Gateway model
service per job (`quote_ai_extract`, `quote_ai_chat`, `quote_ai_embed`), so changing a model is a config change.

## Consequences

- The product facts suite is the evidence; it grows in AI-08 and runs in CI from AI-22, so a model change is tested
  like a code change.
- Prices and available endpoints change: rerun the comparison before changing a model, and update this record.
