# Product facts suite (AI-04; grows in AI-08 and AI-09)

**Glove descriptions (`cases.jsonl`, 100 lines).** Each has a hand label for the AI-04 fields (`material`,
`powder_free`, `sterile`, `length_in`, `texture`) and the AI-08 attribute fields (`textured`, `chemo_claim`,
`use_type`, `latex_free`). `null` means the text does not say: it is a valid answer, and a model that fills it in
anyway has hallucinated (`quote_ai.llm.compare_models.hallucinations`). Lengths are in inches (240 mm = 9.45 in).

- AI-04 scores the first five fields (`quote_ai.llm.compare_models.score`).
- AI-08 scores the attribute fields per attribute (`run_eval.py --suite product_facts`): at least 90% each, zero
  `ai_extraction` rows verified, zero structured GUDID values overwritten (`quote_ai.product.evidence.invariants`).

**Supplier documents (`documents.jsonl`, AI-09).** The truth values of the synthetic supplier documents, written by
the same generator that writes the PDFs (`quote_ai.mock.documents.truth`); a unit test keeps them in step. Field
accuracy compares `quote_ai_product.document_values` against them; the verification cases (Glove A's report, the
report for another catalog number, Glove B's marketing sheet) are unit tests on `quote_ai.product.evidence`.

**Before recording:** cases pf-001 to pf-030 are written in the style of GUDID descriptions, pf-031 to pf-100 are
generated from their labels (so the labels are right by construction), all with `"di": null`. Replace them with 100
real records from `quote_ai_gudid_gold.glove_devices` (put the primary DI in `di`), labelled by hand from the
description only.
