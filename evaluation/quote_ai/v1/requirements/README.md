# Requirements suite (AI-05)

100 quote requests (synthetic, written for DBxLab; 30 in AI-05, 70 added in AI-22) with the fields the extraction must get right: quantity and unit,
sizes, every hard or soft flag, the target price and the `missing` list. Deterministic assertions, no judge:

```
python evaluation/quote_ai/run_eval.py --suite requirements --target dev --catalog dbxarchitectlab_dev
```

Target: 95% field accuracy (`v1/thresholds.yml`). From AI-22 it is part of the release gate: the `quote_ai_eval` job
runs it with the production prompt on every pull request that touches prompts, models, retrieval or agents.

AI-22 added messy requests: requirements in a footnote or a P.S. (`footnote-*`, `ps-*`), soft preferences,
non-sterile-is-fine, delivery windows, prices per box or case, instructions hidden in the text (`injection-*`) and
requests without a material. The footnote cases are the ones a prompt without the footnote rule (v4) misses.

Conventions in the labels: a stated "must / need / required / -free" is `{"value": true, "hard": true}`; "would be
nice" or "preferred" is soft; "non-sterile is fine" is `{"value": null, "hard": false}`; something a quote needs but
the request never says is in `missing` (asked, never guessed); `missing` and `sizes` are compared as sets. Text that
tries to give instructions is data: the injection cases expect no field to change because of it.
