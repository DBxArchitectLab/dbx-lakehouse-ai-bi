"""Train, track and compare (AI-13): four candidates, every run in MLflow, and a selection rule written before the
results (docs/quote_ai/decisions/adr-win-model-selection.md).

  1. rule      the rule baseline (quote_ai.ml.baseline), logged like a model: the bar
  2. logreg    logistic regression: simple, explainable, a second baseline
  3. automl    AutoML's best run: a fast benchmark (notebooks/quote_ai/13_automl.py logs it into the same experiment)
  4. gbt       gradient-boosted trees (scikit-learn HistGradientBoosting), lightly tuned; monotonic in price and lead
               time, so a higher price never scores a higher chance

The selection rule (select): among the candidates with a test calibration error under 0.05 whose test AUC beats the
baseline's by at least 0.05, the highest validation AUC wins. None qualifies: nothing ships, the baseline stays.
Plus one check only a simulation allows (recovers_signal): the winner's top drivers are in the planted signal.

Every run logs the training set table as a dataset input (its Delta version), the git commit, parameters, metrics
for the validation and test months, the calibration bins and the feature importance. The win chance informs the rep;
it never sets a price. Results are on synthetic held-out months: an educational simulation, not a real-sales claim.

The selection logic is plain Python (tests/quote_ai/test_train.py); scikit-learn and MLflow are imported lazily, in
the training job (src/quote_ai/jobs/ml_job.py train).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field

from quote_ai.ml import features as F
from quote_ai.ml import metrics

EXPERIMENT = "/Shared/quote_ai/win"
MAX_CALIBRATION_ERROR = 0.05
MIN_TEST_AUC_GAIN = 0.05
# The planted signal (quote_ai.mock.catalog.SIGNAL) in feature names: price against the customer's current price,
# lead time, stock. A winner's top drivers must come from here.
PLANTED_SIGNAL = ("price_vs_customer_current_pct", "lead_time_days", "stock_cover_days")
CATEGORICAL = ("material", "use_type")
BOOLEAN = ("chemo_verified", "needs_approval")
NUMERIC = tuple(f for f in F.FEATURES if f not in CATEGORICAL + BOOLEAN)
# A higher price, a longer lead time: never a higher chance. needs_approval is true at the lower prices, so it may
# only raise the chance; stock may only help.
MONOTONIC = {"price_vs_customer_current_pct": -1, "price_vs_list_pct": -1, "lead_time_days": -1,
             "needs_approval": 1, "stock_cover_days": 1}
GBT_PARAMS = {"max_iter": 200, "learning_rate": 0.05, "max_leaf_nodes": 15, "min_samples_leaf": 40,
              "l2_regularization": 1.0}


@dataclass
class RunResult:
    name: str
    valid_auc: float
    test_auc: float
    test_calibration_error: float
    test_brier: float = 0.0
    top_features: list[str] = field(default_factory=list)
    run_id: str | None = None


def select(runs: Iterable[RunResult], baseline_test_auc: float, max_calibration_error: float = MAX_CALIBRATION_ERROR,
           min_gain: float = MIN_TEST_AUC_GAIN) -> RunResult | None:
    """The rule written in advance. None = nothing ships; the baseline stays."""
    ok = [r for r in runs if r.name != "rule" and r.test_calibration_error < max_calibration_error
          and r.test_auc >= baseline_test_auc + min_gain]
    return max(ok, key=lambda r: r.valid_auc) if ok else None


def recovers_signal(top_features: Sequence[str], k: int = 2) -> bool:
    """Only a simulation allows this check: the top k drivers are features of the planted signal."""
    return len(top_features) >= k and set(top_features[:k]) <= set(PLANTED_SIGNAL)


def decide(runs: Sequence[RunResult]) -> dict:
    """The selection rule and the signal check over every candidate; the record written to quote_ai_ml.model_selection."""
    (base,) = [r for r in runs if r.name == "rule"]
    winner = select(runs, base.test_auc)
    record = {"baseline_test_auc": base.test_auc, "candidates": [asdict(r) for r in runs]}
    if winner is None:
        return record | {"status": "none", "run_id": None, "run_name": None,
                         "reason": f"no candidate has test calibration error < {MAX_CALIBRATION_ERROR} and test AUC "
                                   f">= {base.test_auc + MIN_TEST_AUC_GAIN:.3f}: the baseline stays"}
    if not recovers_signal(winner.top_features):
        return record | {"status": "none", "run_id": None, "run_name": winner.name,
                         "reason": f"{winner.name} passes the rule but its top drivers {winner.top_features[:2]} are "
                                   f"not the planted signal {list(PLANTED_SIGNAL)}: refused"}
    return record | {"status": "selected", "run_id": winner.run_id, "run_name": winner.name,
                     "reason": f"{winner.name}: valid AUC {winner.valid_auc:.3f}, test AUC {winner.test_auc:.3f} "
                               f"(baseline {base.test_auc:.3f}), calibration {winner.test_calibration_error:.3f}; "
                               f"drivers {winner.top_features[:2]}"}


def evaluate(y_valid, p_valid, y_test, p_test) -> dict[str, float]:
    return metrics.evaluate(y_valid, p_valid, "valid") | metrics.evaluate(y_test, p_test, "test")


def result(name: str, m: Mapping[str, float], importance: Mapping[str, float], run_id: str | None = None) -> RunResult:
    top = [k for k, _ in sorted(importance.items(), key=lambda kv: -kv[1])]
    return RunResult(name, m["valid_auc"], m["test_auc"], m["test_calibration_error"], m["test_brier"], top, run_id)


# ------------------------------------------------------------------ scikit-learn candidates (Databricks job)

def frame(rows):
    """Model inputs as a pandas DataFrame in FEATURES order, booleans as 0/1 (the same in training and scoring)."""
    import pandas as pd
    df = rows if hasattr(rows, "columns") else pd.DataFrame([F.model_input(r) for r in rows])
    df = df[list(F.FEATURES)].copy()
    for c in BOOLEAN:
        df[c] = df[c].astype(float)
    for c in NUMERIC:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
    return df


def make_logreg():
    from sklearn.compose import ColumnTransformer
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler
    prep = ColumnTransformer([
        ("num", Pipeline([("impute", SimpleImputer(strategy="median", add_indicator=True)),
                          ("scale", StandardScaler())]), list(NUMERIC + BOOLEAN)),
        ("cat", OneHotEncoder(handle_unknown="ignore"), list(CATEGORICAL))])
    return Pipeline([("prep", prep), ("model", LogisticRegression(max_iter=2000))])


def make_gbt(params: Mapping | None = None):
    """Gradient-boosted trees, monotonic where the business says so (MONOTONIC), missing values handled natively."""
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder
    prep = ColumnTransformer([("num", "passthrough", list(NUMERIC + BOOLEAN)),
                              ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), list(CATEGORICAL))],
                             verbose_feature_names_out=False).set_output(transform="pandas")
    model = HistGradientBoostingClassifier(monotonic_cst=MONOTONIC, random_state=0, **(params or GBT_PARAMS))
    return Pipeline([("prep", prep), ("model", model)])


def importance(model, X, y) -> dict[str, float]:
    """Permutation importance on the validation months, per input feature (AUC lost when the feature is shuffled)."""
    from sklearn.inspection import permutation_importance
    r = permutation_importance(model, X, y, scoring="roc_auc", n_repeats=10, random_state=0)
    return {c: float(v) for c, v in zip(X.columns, r.importances_mean)}


def dataset_input(table: str, version: int):
    """The training set table as an MLflow dataset (name, Delta version, digest). Verify before recording:
    mlflow.data.load_delta(table_name=..., version=...) on Unity Catalog tables in MLflow 3."""
    import mlflow.data
    return mlflow.data.load_delta(table_name=table, version=version)


def log_run(name: str, model, params: Mapping, m: Mapping[str, float], imp: Mapping[str, float], bins: list[dict],
            dataset, tags: Mapping[str, str], log_model) -> str:
    """One tracked run: dataset input, tags (git commit, training set version), params, metrics, calibration bins,
    feature importance, and the model (log_model(model) does the flavor-specific logging)."""
    import mlflow
    with mlflow.start_run(run_name=name) as run:
        mlflow.set_tags(dict(tags) | {"candidate": name, "demo_data": "true",
                                      "simulation": "synthetic history, planted signal"})
        mlflow.log_input(dataset, context="training")
        mlflow.log_params({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in params.items()})
        mlflow.log_metrics(dict(m))
        mlflow.log_dict({"bins": bins}, "calibration.json")
        mlflow.log_dict(dict(sorted(imp.items(), key=lambda kv: -kv[1])), "feature_importance.json")
        log_model(model)
        return run.info.run_id


def train_all(spark, fe, names: Mapping[str, str], experiment: str, git_commit: str) -> dict:
    """The training job: read the training set, fit and log rule, logreg and gbt, gather every candidate of this
    training set version (AutoML's too) with search_runs, apply the rule, record the decision."""
    import mlflow
    import mlflow.sklearn

    ml = names["ml"]
    table = f"{ml}.win_training_set"
    version = spark.sql(f"DESCRIBE HISTORY {table} LIMIT 1").first()["version"]
    df = spark.table(table).toPandas()
    parts = {s: df[df["split"] == s] for s in ("train", "valid", "test")}
    X = {s: frame(p) for s, p in parts.items()}
    y = {s: p["won"].astype(int).tolist() for s, p in parts.items()}
    mlflow.set_experiment(experiment)
    dataset = dataset_input(table, version)
    tags = {"git_commit": git_commit, "training_set": table, "training_set_version": str(version)}
    # the TrainingSet for fe.log_model: the served model looks up the same features (AI-14)
    labels = spark.table(f"{ml}.quote_labels").join(spark.table(f"{ml}.quote_context_features").drop("sent_at"),
                                                    "quote_id")
    train_labels = labels.join(spark.table(table).filter("split = 'train'").select("quote_id"), "quote_id")
    ts = F.create_training_set(fe, train_labels, names)

    from quote_ai.ml.baseline import RuleBaseline
    rule_model = RuleBaseline().fit(parts["train"].to_dict(orient="records"), y["train"])
    p = {s: rule_model.predict_proba(parts[s].to_dict(orient="records")) for s in ("valid", "test")}
    m = evaluate(y["valid"], p["valid"], y["test"], p["test"])
    log_run("rule", rule_model, rule_model.params(), m, {"price_vs_customer_current_pct": 1.0, "lead_time_days": 1.0},
            metrics.calibration_bins(y["test"], p["test"]), dataset, tags,
            lambda model: mlflow.pyfunc.log_model(name="model", python_model=model.as_pyfunc()))

    def fe_log(model):
        # predict_proba: the served model returns P(lost), P(won) per row (quote_ai.ml.score.win_probability)
        fe.log_model(model=model, artifact_path="model", flavor=mlflow.sklearn, training_set=ts,
                     pyfunc_predict_fn="predict_proba", infer_input_example=True)

    for name, model, params in (("logreg", make_logreg(), {"C": 1.0}), ("gbt", make_gbt(), GBT_PARAMS)):
        model.fit(X["train"], y["train"])
        p = {s: model.predict_proba(X[s])[:, 1].tolist() for s in ("valid", "test")}
        m = evaluate(y["valid"], p["valid"], y["test"], p["test"])
        imp = importance(model, X["valid"], y["valid"])
        log_run(name, model, dict(params) | {"monotonic": MONOTONIC if name == "gbt" else {}}, m, imp,
                metrics.calibration_bins(y["test"], p["test"]), dataset, tags, fe_log)

    runs = []
    found = mlflow.search_runs(experiment_names=[experiment],
                               filter_string=f"tags.training_set_version = '{version}'", output_format="list")
    for r in sorted(found, key=lambda r: r.info.start_time):
        name = r.data.tags.get("candidate")
        if name:
            imp = json.loads(mlflow.artifacts.load_text(f"runs:/{r.info.run_id}/feature_importance.json"))
            runs.append(result(name, r.data.metrics, imp, r.info.run_id))
    latest = {r.name: r for r in runs}            # the newest run of each candidate on this version
    return decide(list(latest.values())) | {"training_set_version": version, "git_commit": git_commit}
