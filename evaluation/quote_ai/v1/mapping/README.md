# Mapping suite (AI-03)

Zero tolerance: every case must pass. Run with `python -m pytest -q tests/quote_ai/test_device_mapping.py`
(the suite joins `evaluation/quote_ai/run_eval.py --suite mapping` in AI-05).

Each line of `cases.jsonl` is one SKU against one GUDID package level, with the package levels of the device
(`di`, `contains_di`, `pkg_quantity`; the DIs are made-up test values, not real GUDID records). The checks:

- `gloves_per_unit` resolves the each, box and case fixtures to 1, 100 (or 200) and 1,000 gloves, and a missing level
  stays unresolved: never a default of 100;
- the automatic match gets the expected confidence (high only when catalog number, unit and count all agree);
- nothing is offerable before a person verifies it, and a package with no DBxLab SKU (or no resolved count) can't be
  verified at all;
- 400 boxes are the expected number of gloves.
