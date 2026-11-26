# AI-21: Multi-agent orchestration and human approval (QuoteAI v3)

One agent or specialists is a hypothesis, measured on the same 100 requests with the rule written first (decision
record 0004). Rules route wherever the route is known; a model routes only open questions, which never change the
state. A manager approves one exact quote version, bound by its terms hash, transactionally: once, completely, on the
version they saw.

| File | What |
| --- | --- |
| `src/quote_ai/agents/orchestrator.py` | `ROUTES` (state -> specialist), `Turn`, `Orchestrator.handle`: action -> handler (QuoteService), question -> model route, else the state's specialist |
| `src/quote_ai/agents/specialists/catalog.py`, `knowledge.py`, `analyst.py`, `pricing.py` | Focused agents with few tools; pricing is rules, not an LLM |
| `src/quote_ai/rules/pricing.py` | `approval_required` (NONE, MANAGER, REFUSED), `discount_band` for the screen |
| `src/quote_ai/rules/approval.py` | `terms_hash`, `check_approver` (no self-approval; DBxLab_Sales_Managers only), `can_issue`; no writes |
| `src/quote_ai/services/quote_service.py` | `approve`: key, version (409), approver rules, one write; `issue`: key, `can_issue`, freshness; `revise_terms`: a new version that needs its own approval; `submit` once |
| `src/quote_ai/rules/sql/lakebase_schema.sql` | `idempotency_key` and `approval` (unique per version and terms hash) |
| `evaluation/quote_ai/experiments/0004_one_vs_specialists.py` | Design A vs B on the e2e cases; `RULE`, `decide`, `write_decision_record` |
| `docs/quote_ai/decisions/0004-orchestration-and-approval.md` | The decision record with the measured numbers |
| `src/quote_ai/app/backend/routers/price.py`, `approvals.py` | `/api/price` (preview, price, submit, issue, revise) and `/api/approvals` (me, inbox, approve one version, reject) |
| `src/quote_ai/app/frontend/src/pages/Pricing.tsx`, `ApprovalInbox.tsx` | The Pricing screen for reps; the inbox for managers only (shown by role, checked again by the API) |
| `resources/quote_ai/quote_ai_app.app.yml` | The managers group may use the app |
| `tests/quote_ai/test_approval.py`, `test_routing.py`, `test_transactions.py`, `test_app_season5.py` | Binding and voiding, self-approval; state -> specialist; concurrent approve, double issue, stale version; the screens' API |
| `evaluation/quote_ai/v1/reliability` (R-013 to R-016), `workflow` (E-001 to E-100), `pricing` (B-001 to B-008) | Concurrency and idempotency; end to end; the bands |

## Deploy and run

```
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run -t dev quote_ai_app
pytest tests/quote_ai/test_approval.py tests/quote_ai/test_routing.py
python evaluation/quote_ai/run_eval.py --suite workflow
python evaluation/quote_ai/experiments/0004_one_vs_specialists.py --endpoint quote_ai_chat_dev --write
```

The app uses design A unless decision record 0004 adopts B (`QUOTE_AI_DESIGN=specialists`). Locally (stand-in) the
experiment keeps A: B uses far fewer tokens but loses 23 quality points on requests that cross specialist lines in
free text (the numbers are in the record; replace them with the recording-day run).

## Verify before recording

- Current guidance for multi-agent systems on Databricks (custom orchestrators on the Agent Runtime; Agent Bricks
  multi-agent options and their status) and the Genie Agent as an MCP tool from the orchestrator.
- Reading the signed-in user and their group membership in Databricks Apps for the approver check
  (`auth.workspace_groups`: SCIM lookup as the app's service principal).
- Lakebase transaction behaviour for the version check (`SELECT ... FOR UPDATE` in `lakebase_repository.py`).

## Differences from the draft

- The draft lists three specialist files; pricing is a fourth module (`specialists/pricing.py`), rules only, as the
  draft's mapping table says ("UC functions (rules), not an LLM decision").
- The quote state table gains APPROVED -> PENDING_APPROVAL, so "back to pending approval" after a change to an
  approved version is an allowed move (`revise_terms`).
- The Lakeside demo's "Glove C with part of the boxes three weeks later" is one revision with delivery_days=21 and
  product GLOVE-C (r3, a new version, the v3 approval void); "back to Glove A in ten days" is the next revision.
- Approving an already-decided version (a second manager after the first) is answered 409 "quote changed; reload",
  as in the concurrent case; a second issue with a new key is refused, so there is never a second issue event.
