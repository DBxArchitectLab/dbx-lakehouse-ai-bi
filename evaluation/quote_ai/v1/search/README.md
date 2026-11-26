# Search suite (AI-17)

50 requests a rep could type: the Lakeside request first, dental (thin, XS, extended cuff), sterile surgical, vinyl,
chemo in small and large sizes, latex, neoprene, polyisoprene, and requests by catalog number. Each case has:

- `need`, `sizes` and the hard `requirements` it states;
- `expected_skus`: the products that should be found, one SKU each (the first size asked), written from the request
  and what each glove is (the eligible ones), never from what search returns;
- `must_not_include`: every other product: offering one as eligible is a violation, however well it ranks (the latex
  glove that looks perfect, the discontinued one, Glove B whose chemo claim is only in its description);
- `account_id`, used by the permission cases.

Scorers (`evaluation/quote_ai/scorers.py`): `recall_at_5`, a metric (one SKU per product, in rank order), and
`eligibility_violations`, a gate that must be 0.

```
python evaluation/quote_ai/run_eval.py --suite search --variant baseline
python evaluation/quote_ai/run_eval.py --suite search --variant hybrid_rerank --mlflow
python evaluation/quote_ai/run_eval.py --suite search --variant leaky_summary   # better search, looser rules: red
```

**AI-23, the held-out slice (package 1.1.0).** S-H01 to S-H12 are messy requests (typos, no catalog words, "can't be
latex", "sz 7.5") written from twelve of the cases above (`from_case`), with that case's labels: the same right answer,
said the way reps actually type. `"split": "heldout"`: written after the AI-17 variants were chosen, so a variant
tuned on S-001 to S-050 is checked on requests it has not seen. Experiment E2 (`hybrid_weighted_rerank`) is measured
on this slice (`evaluation/quote_ai/experiments/run_experiment.py --variable retriever`).
