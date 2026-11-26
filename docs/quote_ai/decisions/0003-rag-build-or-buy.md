# 0003: RAG for compare and explain: build (custom chain) or buy (Knowledge Assistant)

- Status: proposed (AI-16). Accept or change it with the numbers of the 20-question run below.
- Episode: AI-16. Numbering: `0003-win-rate-definition.md` (AI-10) already uses 0003; this file keeps the path the
  AI-16 script shows on screen. Renumber both the file and the script line together if that is preferred (0004).

## Context

The rep has to explain to Lakeside how the three eligible gloves differ, with the evidence behind every sentence.
The table of values comes from data (product evidence, price book, the AI-15 rules); the model only writes the
explanation, from retrieved chunks, with a citation and a truth layer per statement. Two ways to get that:

- **Build**: `quote_ai.llm.rag.explain_comparison`: our retrieval on `quote_ai_ai.knowledge_index` (hybrid,
  section-aware chunks, account filter), the `compare_explain` prompt from the Prompt Registry, the chat model
  through Unity Gateway, then `ground` (code) checks every statement against what it cites.
- **Buy**: Agent Bricks Knowledge Assistant configured on the same documents
  (`resources/quote_ai/quote_ai_knowledge_assistant.notes.md`), asked through its endpoint
  (`quote_ai.llm.knowledge_assistant`).

## How we decide

The same questions, the same scorers (`evaluation/quote_ai/v1/comparison`, C-001 to C-020), never preference:

```
python evaluation/quote_ai/run_eval.py --suite comparison --variant build --endpoint quote_ai_chat_dev --mlflow
python evaluation/quote_ai/run_eval.py --suite comparison --variant buy --ka-endpoint quote_ai_knowledge_assistant --mlflow
```

| Measure | Build | Buy |
| --- | --- | --- |
| truth_layer_violations (gate, 0) | _from the run_ | _from the run_ |
| no_evidence_failures (gate, 0) | | |
| cross_account_violations (gate, 0) | | |
| citation_accuracy | | |
| groundedness judge (sampled, human-reviewed) | | |
| latency, cost per question | | |

## Proposed decision: build, keep buy as the comparison

Reasons that hold whatever the scores (verify each in the workspace):

1. **Account filter before retrieval.** Customer price sheets carry an `account_id`; the build chain filters the
   index by the signed-in rep's accounts inside the query (AI-17). The Knowledge Assistant sees every document it was
   given, so price sheets must stay out of it, and it can't answer account questions.
2. **Truth layers from our data.** Build cites chunk IDs whose truth layer comes from the evidence tables; buy cites
   files, which `to_statements` maps back to our chunks, so a statement with no file citation fails the gate.
3. **Every step traced and versioned**: retrieved chunks, prompt version, latency and tokens in MLflow Tracing.

Buy stays as the baseline the build chain must beat on citation accuracy and groundedness; if it wins clearly on the
gates and the metrics, revisit this decision and add a per-account assistant design.

## Consequences

- We maintain retrieval, the prompt and the grounding check (tested in `tests/quote_ai/test_compare.py`).
- The comparison suite runs on change, not nightly (judges are LLM calls); it joins the release gate in AI-22.
