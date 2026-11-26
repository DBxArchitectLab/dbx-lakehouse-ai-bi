# 0011: Rules-first eligibility with three states, separate from fulfilment

- Status: accepted (written in AI-27, capturing the choice made in AI-15).
- Episode: AI-15.

## Context

"Can this glove do the job?" and "Can we ship it in time?" are different questions. A model asked both at once
blends them, and treats "no evidence" as "probably fine".

## Options

- **(a)** A model ranks candidates and decides.
- **(b)** Search finds candidates; rules decide eligibility, one named check per stated requirement, in three states
  (verified_match, verified_mismatch, unknown); fulfilment is a second rule set (full, partial, backorder, alternative
  date). (Chosen.)

## Decision

(b). `rules/eligibility.py` (`CHECKS`, unknown is never a match) and `rules/fulfilment.py` (a late line is an
alternative that needs the customer's acceptance, which creates r3 and a new version). The same Python runs in
pytest, in the agent's tools and as Unity Catalog functions (`check_eligibility`, `check_fulfilment`). AI Search only
narrows candidates (size, distribution as pre-filters); it never decides.

## Consequences

- The shortlist has three groups: eligible (with fulfilment), needs evidence, excluded (with the failed check).
- Glove C is eligible and late: shown as an alternative, not hidden and not offered as on time.
- A glove that leaves distribution in today's GUDID update is excluded on the next daily run (AI-26).

## Tests

`tests/quote_ai/test_eligibility.py`, `test_fulfilment.py`; the eligibility (60 cases) and fulfilment suites; search's
`eligibility_violations = 0`; the Operations suite (ops-01).
