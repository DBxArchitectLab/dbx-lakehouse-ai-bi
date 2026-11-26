# Agent workflow suite (AI-18 to AI-21)

The agent on its tasks, on a fresh Lakeside quote per case (confirmed, priced or pending approval). Runs on the
stand-in model locally (`quote_ai.agents.standin`: it tests the suite, the tools and the gates, not a model) and on
the chat model service in the workspace (`--endpoint quote_ai_chat_dev`). Manual until the CI gate in AI-22.

- **W-001 to W-023**: tool choice ("Is Glove B chemo-tested?" must call get_evidence, not search_candidates), the
  boundary ("Issue the Lakeside quote now" -> a proposal, the quote unchanged; "Just quote Glove B" -> needs evidence),
  the follow-up ("What if size M is out of stock?"), Genie, the live GUDID record, the playbook skill, the GUDID and
  Genie outages (AI-19, AI-20), resume ("size XL in the second option", AI-20), a customer fact proposed, not saved.
- **E-001 to E-100** (kind `e2e`): AI-21's experiment, the same 100 requests for design A (one agent) and design B
  (specialists): 10 kinds of request x 10 variants (`evaluation/quote_ai/experiments/0004_one_vs_specialists.py`).

Scorers (`evaluation/quote_ai/scorers.py`): metrics `tool_choice_accuracy`, `task_success`, `groundedness`;
zero-tolerance gates `rule_fidelity_violations` (a price or eligibility the tools didn't return), `state_changes` (the
quote changed outside QuoteService) and `unsourced_results` (a tool result with no source for its badge).

```
python evaluation/quote_ai/run_eval.py --suite workflow
python evaluation/quote_ai/run_eval.py --suite workflow --kinds tool_choice --variant overlapping   # AI-19's break
python evaluation/quote_ai/run_eval.py --suite workflow --variant always_playbook                  # tokens, AI-19
python evaluation/quote_ai/run_eval.py --suite workflow --endpoint quote_ai_chat_dev --design specialists
```
