# AI-13: Train, track and compare

A win model chosen by evidence, not by feel: a rule baseline that sets the bar, every candidate tracked in MLflow
with its training data version and git commit, and a selection rule written before the results
([adr-win-model-selection.md](../decisions/adr-win-model-selection.md)). Still the simulation of AI-12: results are
on synthetic held-out months, not a claim about real sales.

| Candidate | Where | What |
| --- | --- | --- |
| `rule` | `src/quote_ai/ml/baseline.py` | Price at or below the customer's current price and lead time within 10 days; its bucket's win rate |
| `logreg` | `src/quote_ai/ml/train.py` | Logistic regression, imputed and scaled |
| `automl` | `notebooks/quote_ai/13_automl.py` | AutoML classification, 20 minutes; logged as a candidate on the same training set version |
| `gbt` | `src/quote_ai/ml/train.py` | HistGradientBoosting, monotonic: a higher price or lead time never raises the chance |

| File | What |
| --- | --- |
| `src/quote_ai/ml/metrics.py` | AUC, Brier, calibration error and bins, precision and recall: one implementation for all four |
| `src/quote_ai/ml/train.py` | `select` (the rule), `recovers_signal`, `decide`; the tracked runs; `train_all` (the job) |
| `src/quote_ai/ml/model_tables.sql` | `model_selection` (and AI-14's tables) |
| `resources/quote_ai/quote_ai_ml_train.job.yml` | `quote_ai_train` |
| `resources/quote_ai/quote_ai_win.experiment.yml` | `/Shared/quote_ai/win` |
| `docs/quote_ai/decisions/adr-win-model-selection.md` | The selection rule, written first |
| `tests/quote_ai/test_train.py` | Known metric answers, the baseline, the rule (None when nothing beats the baseline), the signal check |

## The selection rule

Among candidates with a test calibration error under 0.05 whose test AUC beats the baseline's by at least 0.05, the
highest validation AUC wins; none qualifies: nothing ships. Then the simulation's check: the winner's two top drivers
(permutation importance) must be in the planted signal (`price_vs_customer_current_pct`, `lead_time_days`,
`stock_cover_days`). The decision lands in `quote_ai_ml.model_selection` with the reason.

On the generated data, the dependency-free logistic regression in the tests scores test AUC 0.69 against the rule's
0.63, calibration error 0.04, drivers lead time and price against the current price. The signal's own ceiling (the
true probabilities as scores) is about 0.70, so no model can do much better: expect gbt in the same range. The
margin over the 0.05 bar is about 0.06, so the job's real result decides; verify it before recording.

## Deploy and run

```
databricks bundle deploy -t dev
databricks bundle run quote_ai_ml_features -t dev     # AI-12
# optional, before training: notebooks/quote_ai/13_automl.py (AutoML, logs the "automl" candidate)
databricks bundle run quote_ai_train -t dev
```

Then the experiment `/Shared/quote_ai/win`: select the four runs, compare `valid_auc`, `test_auc`,
`test_brier`, `test_calibration_error`; `calibration.json` and `feature_importance.json` on each run; the dataset
input (win_training_set and its version) and the `git_commit` tag.

## Design notes

- The job runs `src/quote_ai/jobs/ml_job.py train` (the repo's job entry point pattern), which calls
  `quote_ai.ml.train.train_all`; the draft's sketch runs `train.py` directly.
- The decision record lives at the path the script shows, `docs/quote_ai/decisions/adr-win-model-selection.md`,
  not the numbered `0004-...` form of the other records.
- Calibration error is measured on the test months (expected calibration error, 10 bins). With about 500 test
  quotes even the true probabilities score about 0.04 (sampling noise), which is why AI-12 extended the history.
- gbt is logged with `fe.log_model(..., pyfunc_predict_fn="predict_proba")` so the served model returns
  [P(lost), P(won)] and looks up the same features (AI-14). AutoML's runs are a benchmark only: if AutoML wins, the
  register job stops and asks for its configuration to be retrained as a candidate in `train.py`.
- The over-tuned candidate of the script (best validation AUC, test +0.02, calibration 0.09, glove colour as top
  driver) is a test case; DBxLab's data has no colour column.

## Verify before recording

- MLflow 3: `mlflow.data.load_delta(table_name=..., version=...)` and `log_input`, `log_model(name=...)`,
  `search_runs(output_format="list")`, `mlflow.artifacts.load_text`.
- `fe.log_model` arguments in the current databricks-feature-engineering (`artifact_path` or `name`,
  `pyfunc_predict_fn` passed through to the sklearn flavor).
- AutoML: `databricks.automl.classify`, its availability on serverless and in the region, `best_trial.model_path`.
- The serverless environment spec (`environment_version: '2'`, dependencies) for scikit-learn 1.4+ and MLflow 3.
- On screen: the quote history is synthetic demo data.
