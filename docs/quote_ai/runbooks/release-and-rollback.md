# Runbook: release and rollback (AI-25)

Owner: the QuoteAI platform team (DBX_Architect_Lab_Admin). A release is a set of versions in one file,
`release/release.yml`; prod is deployed only from `main`, only by GitHub Actions as sp-dbx-lakehouse, only after the
prod environment's approval. Nobody deploys prod by hand, and prod aliases change only in `cd.yml`.

## A release

1. **Set the manifest** in a pull request: `quote_ai` (the release), `evaluation_package` (= `evaluation/quote_ai/v1/VERSION`),
   the `win_model` version and the prompt versions the full evaluation ran on in dev-shared, the index definition.
2. **CI** (`ci.yml`), cheap checks first: unit tests (including `test_release_manifest.py`), the React build,
   `bundle validate -t dev-shared`, the deterministic suites (`run_eval.py --suites ci`), and, because the pull
   request touches `release/`, the full package in dev-shared (`quote_ai_eval`, all sixteen suites). All green.
3. **Merge to main.** `cd.yml` starts; the `prod` job waits for a reviewer of the GitHub environment `prod`.
4. **Approve.** The prod job, in order:

| Step | What | Fails when |
| --- | --- | --- |
| `build_app.py` | React build (static files the FastAPI back end serves), vendored `quote_ai` | the front end doesn't build |
| `bundle validate -t prod` | the bundle for prod | a resource is wrong |
| `promote --check` | every manifest version exists in `dbxarchitectlab_prod` | a model or prompt version is missing |
| `bundle deploy -t prod` | everything; the win model endpoint on the manifest's version | a deploy error |
| `quote_ai_rules_setup` | eligibility, fulfilment and tool functions; filters, masks, grants | a SQL file fails |
| `quote_ai_promote` | `@champion` on the model and each prompt moved to the manifest | a version is missing |
| `quote_ai_app` | the new app version on quote-ai-prod (front end and back end together) | the app doesn't start |
| `quote_ai_smoke_test` | `/api/health`, then a Lakeside request through the app's API: PRICED, zero violations | any step |

5. **Check prod** (Compute > Apps > quote-ai-prod: resources on the prod warehouse and `_prod` services, CAN_USE for
   the sales groups; Catalog: `quote_ai_ml.win_model` @champion; the prompt aliases). Tag the commit `ai-NN`.

## When the smoke test (or any step) fails

The run stops and its last step prints this runbook. Do not fix prod by hand. Decide:

- **The release is bad** (a regression, a failing smoke test): roll back (below), then fix forward in a pull request.
- **A transient error** (a workspace hiccup, a timeout): re-run the failed job in GitHub; every step is idempotent
  (promotion moves only what differs; the smoke quote is cancelled).

## Rollback in one run

Rollback is boring on purpose: the previous release, through the same pipeline.

```
GitHub > Actions > Deploy > Run workflow   (Use workflow from: main)
  environment: prod
  rollback_to: ai-25          # the previous release tag
```

The prod job checks out the tag (it must be on `main` and have a `release/release.yml`), waits for the approval, then
runs the same steps: deploy the tag's code, rules setup, `quote_ai_promote` moves the aliases back to the previous
manifest's versions, `quote_ai_app` redeploys the previous app version, and the smoke test runs again.

**Aliases only** (a prompt or model regression, the code is fine): restore the previous manifest in a pull request
and merge; the normal release path moves the aliases back.

```
git checkout ai-25 -- release/release.yml
git commit -m "Roll back QuoteAI to the ai-25 manifest"
```

## What changes between targets (configuration only)

| Setting | dev | dev-shared | prod |
| --- | --- | --- | --- |
| catalog | dbxarchitectlab_dev | dbxarchitectlab_dev | dbxarchitectlab_prod |
| deployed by | the developer | GitHub Actions | GitHub Actions, after approval |
| runs as | the developer | sp-dbx-lakehouse | sp-dbx-lakehouse |
| app | quote-ai-dev | quote-ai-dev-shared | quote-ai-prod |
| model services | `quote_ai_*_dev` | `quote_ai_*_dev_shared` | `quote_ai_*_prod` |
| aliases moved by | the register job, experiments | the register job, experiments | `quote_ai_promote` only |

No code reads the target: a difference is a bundle variable or a target override, never `if env == "prod"`.
