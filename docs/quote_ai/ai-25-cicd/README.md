# AI-25: CI/CD for AI, dev to prod

A release is a set of versions, you promote instead of rebuilding, the gates run in order, and rollback is boring.
QuoteAI 1.0 is named in one manifest, the champion aliases move to it after the gates, the prod job deploys the whole
bundle (rules, app included) after an approval, and a smoke test goes through the app's own API.

| File | What |
| --- | --- |
| `release/release.yml` | The release manifest: `quote_ai` 1.0.0, `evaluation_package`, `models.win_model`, `prompts.extraction` / `comparison`, the index and its embedding service |
| `src/quote_ai/ops/promote.py` | `load_manifest`, `validate`, `plan`, `promote` (idempotent, target catalog only), `check`; `MlflowRegistry` (the one MLflow adapter) |
| `src/quote_ai/ops/smoke_test.py` | `smoke`, `run_quote_via_api`: health, then New request -> Confirm -> Shortlist -> Price through the API; PRICED, zero violations; the smoke quote cancelled |
| `src/quote_ai/jobs/release_job.py` | check, promote, smoke: the job entry point |
| `resources/quote_ai/quote_ai_promote.job.yml` | check -> promote |
| `resources/quote_ai/quote_ai_rules_setup.job.yml` | eligibility and tool functions (the existing entry points), then `quote_ai_security_setup` |
| `resources/quote_ai/quote_ai_smoke_test.job.yml` | the smoke test against `quote-ai-<target>` |
| `.github/workflows/cd.yml` | prod: React build, validate, manifest check, deploy (win model endpoint on the manifest's version), rules, promote, app, smoke; `rollback_to`; dev-shared rehearses rules, app, smoke |
| `.github/workflows/ci.yml` | the full package also runs when `release/`, the rules or their config change |
| `docs/quote_ai/runbooks/release-and-rollback.md` | The release, a failed step, rollback in one run, what differs by target |
| `tests/quote_ai/test_release_manifest.py`, `test_promote.py`, `test_smoke_test.py` | Versions whole numbers, the package version, every version in the target catalog (workspace: `QUOTE_AI_RELEASE_CATALOG`); idempotent, never another catalog, rollback; the smoke test on the API |

## Deploy and run

```
python -m pytest -q tests/quote_ai/test_release_manifest.py tests/quote_ai/test_promote.py tests/quote_ai/test_smoke_test.py
databricks bundle validate -t prod
databricks bundle deploy -t dev-shared
databricks bundle run quote_ai_eval -t dev-shared
PYTHONPATH=src python -m quote_ai.ops.promote --catalog dbxarchitectlab_prod --check
# prod: merge to main and approve in GitHub (cd.yml); never by hand
# rollback: Actions > Deploy > Run workflow, environment prod, rollback_to <previous tag>
```

## Verify before recording

- `databricks bundle run quote_ai_app` deploys the app's source (and whether `bundle deploy` already does).
- `${resources.apps.quote_ai_app.name}` as a job parameter; the Apps proxy accepting a service principal's OAuth token
  (the smoke test), its forwarded identity, and that identity's CAN_USE (the target's top-level CAN_MANAGE) and its
  access to the Lakeside account (a `rep_account` row, or the managers group).
- MLflow 3 prompt alias APIs (`mlflow.genai.set_prompt_alias`, `load_prompt("prompts:/name@alias")`,
  `load_prompt("prompts:/name/N")`) and `MlflowClient.get_model_version_by_alias`.
- How a model and prompt version reach `dbxarchitectlab_prod`: the manifest's versions must exist there
  (`promote --check`); registering them in prod (the train/register job in prod, or `copy_model_version` from the
  dev catalog, and `prompts.register` in prod) is a step to agree before the first prod release.
- Region availability of every prod model service; prod gateway limits and budgets.
- All code, rules and configuration are in the bundle (the AI-25 dependency gate).

## Differences from the draft

- The manifest names the evaluation package in `v1/VERSION` (the test requires it), not the script's "one point
  one": 1.2.0 when AI-25 is recorded (since AI-24), 1.3.0 from AI-26 (the Operations suite).
- The manifest's index is `sku_search_index` (the index the code builds), not `gloves_hybrid`; prompts are keyed by
  use (`extraction`, `comparison`) and map to the registered names `extract_requirements`, `compare_explain`.
- `release/release.yml` sits outside a `quote_ai` folder, as the plan and the script show it; it is QuoteAI's only
  file outside one (its first key is `quote_ai`).
- Rollback is one workflow run with `rollback_to: <tag>` (the tag's code and manifest, the same gates), in addition to
  the draft's restore-the-manifest pull request for an alias-only rollback.
- The rules setup is a new job, `quote_ai_rules_setup`, that calls the existing entry points (search_job functions,
  agent_job functions) and runs `quote_ai_security_setup`; nothing is copied.
