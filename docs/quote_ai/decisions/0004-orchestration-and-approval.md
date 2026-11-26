# 0004: Orchestration (one agent or specialists) and human approval

- Status: proposed (AI-21). The numbers below are from the local stand-in model; replace them with the run on the
  chat model service on recording day (`--endpoint quote_ai_chat_dev --write`) and accept or change the decision.
- Episode: AI-21. Number: the next free one (0003 is used twice already: `0003-win-rate-definition.md` and
  `0003-rag-build-or-buy.md`; `adr-win-model-selection.md` has no number).

## Context

By the end of AI-20 one agent does product search, evidence questions, pricing, sales analytics and the playbook. Its
prompt and tool list keep growing, and its mistakes get harder to trace. The common answer is several focused
specialists behind one entry point. That is a hypothesis, not a destination: specialists may choose tools better, or
only add hand-offs, latency and cost.

Separately, approval was only words: the agent could say "a manager must approve", and nothing happened.

## Decision 1: one agent or specialists, measured

- **Design A**: one agent inside the workflow (QuoteAgent with every tool), as at the end of AI-20.
- **Design B**: four specialists (catalog, knowledge, analyst through Genie, pricing by rule) behind a rule-based
  orchestrator (`src/quote_ai/agents/orchestrator.py`): rules route by quote state and explicit actions; a model
  routes only open questions; questions never change the state.
- **Method**: the same 100 end-to-end requests (`evaluation/quote_ai/v1/workflow`, kind `e2e`) through the same
  package, with the same model, tools and gateway; only the design differs.
- **Rule, written before the run**: adopt B only if it gains at least 5 points of quality (the mean of task success,
  tool-choice accuracy and groundedness) at no more than 20% extra tokens and 20% extra p95 latency per request;
  otherwise keep A. A gate above zero (rule fidelity, state changes, unsourced results) disqualifies a design.

```
python evaluation/quote_ai/experiments/0004_one_vs_specialists.py --endpoint quote_ai_chat_dev --write
```

<!-- RESULTS:BEGIN -->
Run 2026-11-25, model: stand-in (deterministic, local), 100 requests (workflow suite, kind e2e).

| Measure | A: one agent | B: specialists |
| --- | --- | --- |
| Quality (points) | 100.0 | 77.0 |
| Task success | 1.0 | 0.63 |
| Tool-choice accuracy | 1.0 | 0.68 |
| Groundedness | 1.0 | 1.0 |
| Tokens per request | 9401.1 | 3518.2 |
| p95 latency (s) | 0.0786 | 0.0762 |

Gates (must be 0): A {"rule_fidelity_violations": 0, "state_changes": 0, "unsourced_results": 0}; B {"rule_fidelity_violations": 0, "state_changes": 0, "unsourced_results": 0}

**Decision by the rule: keep A (one agent).**
<!-- RESULTS:END -->

Reading the stand-in run: B is far cheaper per request (short prompts, few tools, pricing with no model at all), but
it loses on the requests that cross specialist lines in free text ("Can we do $8 a box on Glove A?", "Lakeside wants
12% off", "Issue the quote now"): by design those go to the Pricing screen's buttons, not to a chat answer. Whether
the real model closes that gap is what the recording-day run decides. Either outcome is the lesson: specialists earn
their hand-offs, or we avoid complexity paid for on every request. The app uses the design the record keeps
(`QUOTE_AI_DESIGN=specialists` switches the chat to design B).

## Decision 2: human approval, bound to one version, transactional

- **Policy (demo data, `config/quote_ai/policy.yml`)**: up to 5% below list, the rep may issue (PRICED -> APPROVED by
  rule); over 5% up to 15%, a sales manager approves; above 15%, or below the margin floor, refused by
  `quote_ai.rules.pricing.approval_required`, and no approval can override the margin floor. The price itself is
  `price_quote`'s (AI-18). Lakeside: $8.60 list, $8.00 asked, 7%: a manager approves.
- **Bound to a version**: the approval stores `quote_ai.rules.approval.terms_hash` of the quote, its version, the
  confirmed requirements version (r2, r3), the delivery date, the evidence and inventory snapshots, each line's SKU,
  boxes and unit price, and the discount. Any change (a price edit, a later date the customer accepts, another
  product) is a new version through `QuoteService.revise_terms`, so the old approval no longer matches and the quote
  goes back to PENDING_APPROVAL (the state table gained APPROVED -> PENDING_APPROVAL for this). `can_issue` is true
  only for the latest approval of this version and hash.
- **Who**: members of DBxLab_Sales_Managers, read from the signed-in identity (X-Forwarded-Email, the group looked up
  for it), never a field in the request; never the quote's own rep. The React menu hides the Approval inbox by role;
  the API checks again (403).
- **Transactional**: an idempotency key per click on submit, approve and issue (a repeat returns the first result);
  optimistic locking on the version the manager saw (409 "quote changed; reload"); the approval row, the state change
  and the quote_event commit in one Lakebase transaction, or not at all (`storage/lakebase_repository.py`).

## Options considered

- A supervisor LLM that decides everything, including whether approval is needed: flexible, unpredictable where it
  matters most. Rejected. (Agent Bricks' Supervisor Agent is legacy for this; our orchestrator is our own code on the
  Agent Runtime.)
- A fixed workflow with no agents: predictable, but no free-text follow-ups. Rejected.
- Chosen: rules over the state machine; LLM specialists for free text only if they beat one agent (decision 1);
  pricing and approval by rule; human approval bound to a version.

## Consequences

- Zero issued quotes without a matching approval and fresh inputs (`QuoteService.issue`: `can_issue` and the
  freshness policy of AI-20), tested in `tests/quote_ai/test_transactions.py` and the reliability suite (R-013 to
  R-016).
- Every transition and approval is in quote_event with identity, version and key, synced to Delta for reporting
  (`quote_ai_sales.quote_event`, `quote_approval`).
- The decision on design B is re-run whenever the model behind quote_ai_chat changes.
