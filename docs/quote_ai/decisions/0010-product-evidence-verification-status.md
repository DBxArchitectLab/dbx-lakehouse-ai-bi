# 0010: product_evidence, with a source and a verification status on every row

- Status: accepted (written in AI-27, capturing the choices made in AI-08 and AI-09).
- Episode: AI-08 (GUDID fields and AI values), AI-09 (supplier documents).

## Context

A glove's description says "chemo tested". A model reading it is confident. Neither is a test result. Quoting on an
extracted claim would offer a glove nobody can prove is suitable.

## Options

- **(a)** One attribute table with the "best" value per glove.
- **(b)** Every claim as an evidence row with its source, method and verification status, in four kinds of truth:
  source truth, extracted claim, business truth, generated answer. Only verified rows (business truth) decide.
  (Chosen.)

## Decision

(b). `quote_ai_product.product_evidence` (GUDID structured fields, verified; AI-extracted values, always unverified)
and `supplier_quality_evidence` (a supplier document passes five checks, the last a QA sign-off by a person), read
together through `glove_evidence`. `product/evidence.py` sets precedence and conflicts; an AI-extracted row is never
verified by code. Every answer names its truth layer.

## Consequences

- Glove B stayed unknown until its report arrived and was signed off (AI-26); then the next daily run made it
  business truth and eligibility changed with no prompt touched.
- The app's evidence badges show verified, unverified, conflicting and superseded.
- The quote version pins the evidence snapshot it was decided on.

## Tests

`tests/quote_ai/test_evidence.py`, `test_document_evidence.py`, the product facts suite (`status_violations = 0`),
the Operations suite (ops-02).
