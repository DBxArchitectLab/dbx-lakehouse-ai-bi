# 0012: Deterministic pricing; the prediction informs, never sets

- Status: accepted (written in AI-27, capturing the choices made in AI-14, AI-18 and AI-21).
- Episode: AI-18 (price_quote), AI-14 (the win chance), AI-21 (discount bands).

## Context

A model can suggest a price that "wins". If it sets the price, a hallucinated tier, a wrong unit or an over-eager
discount reaches the customer, and nobody can explain the number.

## Options

- **(a)** The agent or the win model proposes the price, a person accepts it.
- **(b)** `price_quote` computes every price from the price book (effective-dated tier, units from the verified
  packaging, the margin floor, the row ID it used); the discount bands of `config/quote_ai/policy.yml` decide whether
  a manager approves; the win chance is shown next to candidate prices as information. (Chosen.)

## Decision

(b). `rules/pricing.py` is the only price; the agent calls it as a tool and may not state a price it did not return
(`agents/boundary.py`). The win chance (`win_model@champion`, a simulation on synthetic data, 0007) is read-only:
`/api/win-chance` changes nothing on the quote. Lakeside: Glove A at $8.60 list; $8.00 asked is 7% off, over the rep's
5%, so that quote version goes to a manager.

## Consequences

- Every price line cites its price book row; the same inputs give the same price, in pytest, the agent and SQL.
- Cost and margin are masked from reps (AI-24); the margin floor still applies through `floor_price`.
- A price injected in a request email changes nothing (sec-01).

## Tests

`tests/quote_ai/test_pricing.py`, `test_pricing_injection.py`, `test_agent_boundary.py`; the pricing suite
(`errors = 0`); the capstone app flow (PENDING_APPROVAL on version 2, approved, issued).
