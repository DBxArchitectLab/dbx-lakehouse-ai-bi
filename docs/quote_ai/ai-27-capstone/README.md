# AI-27: Capstone - from a quote request to an issued quote

No new features. The whole Lakeside transaction through the app's own API, measured against every success criterion
of the plan (section 10) on one scorecard; six decision records written for choices made along the way; the
architecture, a deployment guide from an empty workspace, and a retrospective.

| File | What |
| --- | --- |
| `evaluation/quote_ai/capstone/app_flow.py` | The Lakeside flow through `/api/*`: request, r1 with what is missing, r2, shortlist (A, B, C, D eligible; E excluded; C the alternative), compare and ask, Ask the data, price $8.00 (7%), submit, manager approves version 2, issue, the hood (snapshots, quote_event, Ops) |
| `evaluation/quote_ai/capstone/run_capstone.py` | The scorecard: 16 criteria, each mapped to the suite metrics or check that prove it; pure-code suites run, workspace suites read from the latest gate run, production freshness and cost, the deployed app's health; writes `quote_ai_eval.capstone` |
| `resources/quote_ai/quote_ai_capstone.job.yml` | The scorecard in a target, read-only |
| `evaluation/quote_ai/security.py` | `app(store=...)`: the demo back end on another evidence snapshot (Glove B's report arrived) |
| `docs/quote_ai/decisions/0007` to `0012` | Real GUDID + synthetic data; SKU model; state machine and approval per version; product_evidence; rules-first eligibility; deterministic pricing |
| `docs/quote_ai/architecture.md`, `deployment-guide.md`, `retrospective.md` | The architecture in six ideas and the parts; empty workspace to prod in order; what worked, what didn't |
| `README.md` | Links to the architecture and the deployment guide |
| `tests/quote_ai/test_capstone.py` | The end-to-end flow, Glove B before its report, the scorecard's coverage of section 10, a failing criterion fails, the job, the twelve records |

## Run

```
python -m pytest -q tests/quote_ai/test_capstone.py
python evaluation/quote_ai/capstone/run_capstone.py --baseline-minutes 95 --app-minutes 6
databricks bundle run quote_ai_eval -t dev-shared
databricks bundle run quote_ai_capstone -t prod --params baseline_minutes=95,app_minutes=6
git tag ai-27     # then the GitHub release "QuoteAI 1.0" with the deployment guide and the scorecard
```

Locally the scorecard passes on the pure-code suites and the app flow, and lists as not_run what needs the workspace:
requirement extraction, product facts and attributes, the comparison judge, Genie, the win model's held-out months,
model access, production freshness and cost, and (without a recorded baseline) the business outcome.

## Verify before recording

- End-to-end tests and a deployment rehearsal pass (the dependency gate): `quote_ai_smoke_test` in dev-shared.
- The by-hand baseline recorded for the time comparison; the in-app time measured in the demo.
- `quote_ai_eval.eval_results` holds a gate run in the target the capstone reads (prod reads its own catalog).
- Every product name in the deck against current docs; demo data labels on screen; real brands pseudonymized.

## Differences from the draft and the script

- The script lists "thirteen suites of the package" plus Genie, the win model, operations and the app; the
  scorecard maps the plan's sixteen criteria to the sixteen suites of v1/thresholds.yml (operations is a suite since
  AI-26) and the app flow.
- The win chance is asked after Glove A is chosen and priced: the route needs the quote's SKUs (`/api/win-chance`
  answers 409 before); it is read-only and changes nothing.
- In prod the Lakeside flow runs in-process on the demo data (read-only); creating a real quote there is the demo's
  manual part, in the app.
- The draft's "0001..0012 reviewed and completed": the six existing records are unchanged; `0003-win-rate-definition`
  and `adr-win-model-selection` stay as they are (the numbering clash is listed in the retrospective).
