# Databricks notebook source
# MAGIC %md
# MAGIC # AI-13: AutoML, a fast first look (candidate 3 of 4)
# MAGIC AutoML classification on the training months of quote_ai_ml.win_training_set, a 20-minute budget. It writes its
# MAGIC own experiment and a notebook for its best model, which you can read and rerun. Then the best model is measured
# MAGIC with the same metrics as the other candidates (quote_ai.ml.metrics) on the validation and test months, and
# MAGIC logged as the candidate "automl" in /Shared/quote_ai/win with the same training set version, so the training
# MAGIC job's selection rule sees it. **Synthetic demo data, a planted signal: an educational simulation.**
# MAGIC Verify before recording: the AutoML API (databricks.automl.classify), serverless support and region availability.

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

import mlflow  # noqa: E402
import mlflow.sklearn  # noqa: E402
from databricks import automl  # noqa: E402

from quote_ai.ml import features as F  # noqa: E402
from quote_ai.ml import train as T  # noqa: E402

dbutils.widgets.text("catalog", "dbxarchitectlab_dev")  # noqa: F821
dbutils.widgets.text("prefix", "")  # noqa: F821  dev_<user>_ in a personal dev copy
dbutils.widgets.text("experiment", T.EXPERIMENT)  # noqa: F821
CAT, PRE = dbutils.widgets.get("catalog"), dbutils.widgets.get("prefix")  # noqa: F821
TABLE = f"{CAT}.{PRE}quote_ai_ml.win_training_set"
version = spark.sql(f"DESCRIBE HISTORY {TABLE} LIMIT 1").first()["version"]  # noqa: F821
df = spark.table(TABLE)  # noqa: F821
train_df = df.filter("split = 'train'").select(*F.FEATURES, F.LABEL)

# COMMAND ----------

summary = automl.classify(train_df, target_col=F.LABEL, primary_metric="roc_auc", timeout_minutes=20)
best = summary.best_trial
print(best.mlflow_run_id, best.metrics.get("val_roc_auc_score"), best.notebook_url)

# COMMAND ----------

model = mlflow.sklearn.load_model(best.model_path)     # the scikit-learn pipeline AutoML trained
data = df.toPandas()
parts = {s: data[data["split"] == s] for s in ("valid", "test")}


def p_win(frame):
    return [float(x) for x in model.predict_proba(frame[list(F.FEATURES)])[:, 1]]


m = T.evaluate(parts["valid"][F.LABEL].tolist(), p_win(parts["valid"]),
               parts["test"][F.LABEL].tolist(), p_win(parts["test"]))
mlflow.set_experiment(dbutils.widgets.get("experiment"))  # noqa: F821
with mlflow.start_run(run_name="automl"):
    mlflow.set_tags({"candidate": "automl", "training_set": TABLE, "training_set_version": str(version),
                     "automl_run_id": best.mlflow_run_id, "demo_data": "true"})
    mlflow.log_input(T.dataset_input(TABLE, version), context="training")
    mlflow.log_metrics(m)
    imp = T.importance(model, parts["valid"][list(F.FEATURES)], parts["valid"][F.LABEL].tolist())
    mlflow.log_dict(dict(sorted(imp.items(), key=lambda kv: -kv[1])), "feature_importance.json")
print({k: round(v, 3) for k, v in m.items() if k.endswith(("auc", "calibration_error"))})
