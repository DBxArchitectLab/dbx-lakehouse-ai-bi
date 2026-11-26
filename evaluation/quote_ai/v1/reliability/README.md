# Reliability suite (AI-18 to AI-21)

Zero tolerance. Each case names a **scenario** (`evaluation/quote_ai/season5.scenario`) run against the real code,
with fakes only for the part that is slow or failing, and the outcome it must have.

| Episode | Cases | What must hold |
| --- | --- | --- |
| AI-18 | R-001 to R-004 | a tool that times out is retried 3 times (1, 2, 4 s) then a clear stop; a PricingError is not retried; the same call twice stops the loop (no progress); the step limit (8) stops a confused model |
| AI-19 | R-005 to R-008 | the same live lookup twice is one API call; Genie down: the answer names the missing history, invents no prices, still answers Glove A, and Genie is called once; PERMISSION_DENIED is never retried; the same proposal twice is one proposal |
| AI-20 | R-009 to R-012 | GUDID down: the lakehouse copy, labelled with its release date; GUDID evidence older than 48 h blocks ISSUED and is named; fresh inputs issue; a migration whose counts or checksums differ stops and leaves nothing copied |
| AI-21 | R-013 to R-016 | two managers approve v3 at once: one APPROVED, one 409, one approval row; issue twice with one key: one ISSUED, one event; a stale version: 409; a later date the customer accepts: r3, a new version, back to PENDING_APPROVAL, can_issue false |

```
python evaluation/quote_ai/run_eval.py --suite reliability      # CI, every pull request
```
