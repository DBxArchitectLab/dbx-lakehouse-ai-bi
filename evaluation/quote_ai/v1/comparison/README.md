# Comparison suite (AI-16, grown in AI-17)

30 questions about the Lakeside shortlist (C-001 to C-020 in AI-16, C-021 to C-030 in AI-17): explain the
differences, Ask about this product on Glove A (verified chemo report), Glove B (claim only), questions the documents
cannot answer, and permission cases (rep.bob, the draft's rep_north, asking about Lakeside's price sheet; rep.alice,
rep_south, asking about Harbor Point's).

Each case: `question`, `scope` (the gloves it is about), `rep` (whose accounts filter retrieval),
`expected_sources` (document IDs; `table` for a table value, `rule` for a rule result), `expect_no_evidence`.

Gates (must be 0): `truth_layer_violations` (every statement has a citation and a layer; business truth points to
verified evidence), `cross_account_violations` (nothing retrieved from another rep's account),
`no_evidence_failures`. Metrics: `citation_accuracy` (the cited source supports the statement), `source_recall`, and
with `--mlflow` the grounding judge, with a human reviewing a sample of its decisions in the review app.

```
python evaluation/quote_ai/run_eval.py --suite comparison --variant build --endpoint quote_ai_chat_dev
python evaluation/quote_ai/run_eval.py --suite comparison --variant buy --ka-endpoint quote_ai_knowledge_assistant
```
Without `--endpoint` the build variant uses the extractive stand-in (quotes the retrieved text): CI and local runs.
