# Pricing suite (AI-18, bands in AI-21)

Zero tolerance; pure code, so it runs on every pull request next to the eligibility suite. 23 cases.

- **P-001 to P-015** (AI-18): `quote_ai.rules.pricing.price_quote` on a fixed book and packaging
  (`evaluation/quote_ai/season5.BOOKS`, `PACKAGING`): 400 boxes are 400 boxes, not 400 gloves; 10 cases of 10 boxes
  are 100 boxes; the tier break at 250 boxes; the contract tier; a book price below the margin floor raises; an
  unknown unit, a part box, a SKU without packaging data and a tier without a row are errors, never a guess; the row
  effective on the day.
- **B-001 to B-008** (AI-21): `approval_required`, the discount bands of `config/quote_ai/policy.yml`: 4% and exactly
  5% NONE; 7% (Lakeside's $8.00 against $8.60), 10% and exactly 15% MANAGER; 16%, 20% and anything below the margin
  floor REFUSED (no approval can override the floor).

Expectations come from the price book and the policy, never from what the code returns.

```
python evaluation/quote_ai/run_eval.py --suite pricing          # CI, every pull request
```
