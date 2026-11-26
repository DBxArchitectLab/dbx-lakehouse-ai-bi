# Win model suite (AI-13)

Measured on synthetic held-out months: a simulation, not a sales claim.

| Where | What | Passes when |
| --- | --- | --- |
| CI (`cases.jsonl`) | The selection rule written in advance (`quote_ai.ml.train.decide`) on four candidate sets | `decision_failures` = 0: the expected status and winner every time |
| Workspace (the `quote_ai_eval` job, `--catalog`) | The latest row of `quote_ai_ml.model_selection` written by `quote_ai_train` | `beats_baseline` = 1 and `signal_recovered` = 1 |

```
python evaluation/quote_ai/run_eval.py --suite win_model
```
