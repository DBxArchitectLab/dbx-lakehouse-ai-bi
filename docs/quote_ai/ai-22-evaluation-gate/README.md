# AI-22: Evaluation as a release gate

Every test collected since AI-02 becomes one versioned package, `evaluation/quote_ai/v1` (1.0.0): sixteen suites, one
release rule (`v1/thresholds.yml`), one runner that exits 1 below a threshold. Assertions for the high-risk suites,
metrics for retrieval and the win model, a judge only for free-text comparison. A pull request that makes QuoteAI
worse fails like a failing unit test, and reps' thumbs-down answers become candidate cases.

| File | What |
| --- | --- |
| `evaluation/quote_ai/v1/thresholds.yml` | The release rule: 16 suites, each with its episode, `required`, `runs` (ci, workspace, separate, monitored) and a threshold per metric. 14 required; data authorization (AI-24) and operations (AI-26) declared, not required yet |
| `evaluation/quote_ai/v1/VERSION` | `1.0.0`, written with every result row |
| `evaluation/quote_ai/gate.py` | `load_thresholds`, `select` (ci, all, required, a list), `check` (pass, fail, not_run), `verdict`, `result_rows`, `write_results`, `table` |
| `evaluation/quote_ai/run_eval.py` | `--suites ci | all | a,b`: every suite through its existing runner, renamed to the rule's metrics (`collect`), checked, written to `eval_results`, exit 1 when a required suite fails. New suites: `ingestion`, `win_model`, `model_access` (`--outcomes`), `data_authorization` |
| `evaluation/quote_ai/scorers.py` | `no_ineligible_options`, `unknown_as_match`, `layer_cited`, `judge_human_agreement`, `grounded_judge` (the `grounded_comparison` guideline judge), `tool_order_ok` and `eligibility_before_compare` (the trace version), `illegal_transitions` |
| `evaluation/quote_ai/season4.py` | `predict_shortlist`: the Lakeside shortlist case (elig-007), requirements from the request text when an extractor is given |
| `evaluation/quote_ai/season5.py` | The workflow suite records each quote move (`illegal_transitions`); runs on `quote_ai.fixtures`, not on test helpers |
| `src/quote_ai/fixtures/` | The Lakeside fixtures and the Season 5 helpers, moved from `tests/quote_ai` so the package runs without pytest (the test modules re-export them) |
| `src/quote_ai/llm/prompts.py` | `extract_requirements` v4 = v3 + the footnote rule (`FOOTNOTE_RULE`); `PRODUCTION = "v4"` |
| `evaluation/quote_ai/v1/requirements/cases.jsonl` | 30 -> 100 cases: footnotes, P.S., soft wording, delivery, prices, injection, no material |
| `evaluation/quote_ai/v1/eligibility/cases.jsonl` | + elig-007 (the Lakeside shortlist, latex-free in a footnote); Glove B's pair is E-014 / E-015 |
| `evaluation/quote_ai/v1/{ingestion,win_model}/cases.jsonl`, `data_authorization/`, `operations/` | The suites the package was missing |
| `evaluation/quote_ai/eval_tables.sql` | `eval_results` (one row per suite and metric, git SHA, package version, model calls, cost) and `candidate_cases` |
| `resources/quote_ai/quote_ai_eval.job.yml` | The `quote_ai_eval` job: `--suites all` with the extraction model, Genie, the judge through Unity Gateway; runs as sp-dbx-lakehouse |
| `.github/workflows/ci.yml` | `run_eval.py --suites ci` on every pull request; the `eval-gate` job runs `quote_ai_eval` in dev-shared when a pull request touches `src/quote_ai/llm`, `search`, `agents`, `config/quote_ai/models.yml` or the package |
| `src/quote_ai/app/backend/routers/feedback.py`, `backend/feedback.py` | `POST /api/feedback`: `rep_rating` on the answer's trace (source HUMAN, the signed-in rep); a thumbs down -> `quote_ai_eval.candidate_cases` |
| `src/quote_ai/app/frontend/src/components/Feedback.tsx` | Thumbs up or down and a comment under every chat and compare answer |
| `tests/quote_ai/test_gate.py`, `test_scorers.py`, `test_app_season6.py` | The rule, the runner, the regressions it blocks; the new scorers on toy data; the feedback route |

## Deploy and run

```
python evaluation/quote_ai/run_eval.py --suites ci            # what every pull request runs
databricks bundle validate -t dev
databricks bundle deploy -t dev
databricks bundle run quote_ai_eval_tables -t dev             # the case and results tables
databricks bundle run quote_ai_eval -t dev                    # the whole package, with the judge
pytest tests/quote_ai
```

The demo's regression, locally: `python evaluation/quote_ai/run_eval.py --suite requirements --prompt-version v3
--catalog <catalog>` reads with the prompt that has no footnote rule; in the job, elig-007 then puts Glove E on the
shortlist and the gate fails.

## Verify before recording

- `mlflow.genai.evaluate`, `@scorer`, `Guidelines(name=, guidelines=)` and pointing a judge at a gateway endpoint
  (`scorers.grounded_judge`); the judge's metric name in `results.metrics` (`grounded_comparison/mean`).
- `mlflow.log_feedback` and `AssessmentSource` (`backend/feedback.py`); `mlflow.get_last_active_trace_id`
  (`routers/compare.py`).
- The review app / labeling sessions: their export to `--review-labels` (`[{"judge": bool, "human": bool}]`).
- `trace.search_spans()` and the tool span names (`scorers.eligibility_before_compare`).
- Job parameters in a `spark_python_task` (`{{job.parameters.suites}}`) and how the CI job deploys a pull request's
  code to dev-shared before running the job.
- The `model_selection` columns the win model's workspace check reads (`run_eval.latest_selection`).

## Differences from the draft

- The draft's latex SKU `DBX-LTX-220-M` is not in the synthetic catalog: Glove E (latex) is `GLOVE-E-*`, catalog
  number `DBX-NL-<size>-100`. elig-007 uses those.
- The draft's Glove B pair elig-012a/b already exists as E-014 (`product_evidence@v41`) and E-015 (`@v57`).
- elig-007 runs in the eligibility suite as a shortlist case (`kind: shortlist`); its options are what the Shortlist
  screen lists (eligible and needs evidence), so a latex glove waiting for a chemo report counts as reaching it.
- The draft's job sketch points at `evaluation/run_eval.py`; the runner is `evaluation/quote_ai/run_eval.py`.
- Sixteen suites declared, fourteen required: data authorization becomes required in AI-24; operations stays
  monitored (AI-26).
