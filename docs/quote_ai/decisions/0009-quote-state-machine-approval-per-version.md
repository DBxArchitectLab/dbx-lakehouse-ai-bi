# 0009: One quote state machine; approval bound to a quote version

- Status: accepted (written in AI-27, capturing the choices made in AI-07 and AI-21).
- Episode: AI-07 (the states and QuoteService), AI-21 (approval).

## Context

Many things want to change a quote: screens, the agent, jobs, retries, two managers at once. If any of them can write
a state, a discount can slip through, or an approved quote can change after its approval.

## Options

- **(a)** States as a column anyone may update; checks in each caller.
- **(b)** One table of allowed moves, one service that commits every change, and approval of an exact version.
  (Chosen.)

## Decision

(b). `rules/quote_states.py` holds `ALLOWED`, every move in one table; `QuoteService` is the only code that commits a
change (`_move`: the table, optimistic locking on the revision, idempotency keys, one `quote_event` per move). A
discount outside the rep's limit sends that version to PENDING_APPROVAL; the manager approves the version and its
`terms_hash` (`rules/approval.py`: managers only, no self-approval). Any change to the terms is a new version and the
approval is void (AI-21: APPROVED -> PENDING_APPROVAL). The agent proposes; a person confirms; QuoteService decides.

## Consequences

- Every issued quote carries its approval history; a stale screen gets 409 "quote changed; reload".
- Double clicks and retries return the first result (one event).
- The same table runs on Delta (AI-07) and Lakebase (AI-20): storage changed, the rules did not.

## Tests

`tests/quote_ai/test_quote_states.py`, `test_quote_service.py`, `test_approval.py`, `test_transactions.py`; the
workflow and reliability suites; the capstone's app flow (`evaluation/quote_ai/capstone/app_flow.py`).
