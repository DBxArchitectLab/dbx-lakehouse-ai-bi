# Databricks notebook source
# MAGIC %md
# MAGIC # AI-14: inject drift, and watch the monitor catch it
# MAGIC **Demo data.** A month where competitors cut prices and every customer's current price falls by about 8%
# MAGIC (quote_ai.mock.generate_dbxlab.drift_month, rows tagged source = 'drift demo'). Then rerun the features and the
# MAGIC nightly scoring: our prices now look higher against what customers pay, so price_vs_customer_current_pct moves
# MAGIC and the win chances fall. The monitor on quote_ai_ml.open_quote_scores flags both.
# MAGIC Undo: `DELETE FROM <catalog>.<prefix>quote_ai_sales.customer_price_history WHERE source = 'drift demo'`.

# COMMAND ----------

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

from quote_ai.mock import generate_dbxlab  # noqa: E402
from quote_ai.ml import monitor  # noqa: E402

dbutils.widgets.text("catalog", "dbxarchitectlab_dev")  # noqa: F821
dbutils.widgets.text("prefix", "")  # noqa: F821  dev_<user>_ in a personal dev copy
CAT, PRE = dbutils.widgets.get("catalog"), dbutils.widgets.get("prefix")  # noqa: F821
PRICES = f"{CAT}.{PRE}quote_ai_sales.customer_price_history"
SCORES = f"{CAT}.{PRE}quote_ai_ml.open_quote_scores"

# COMMAND ----------

# 1. The drift month
rows = generate_dbxlab.drift_month(end=datetime.now(timezone.utc))
schema = spark.table(PRICES).schema  # noqa: F821
drift_df = spark.createDataFrame([tuple(r[f.name] for f in schema.fields) for r in rows], schema)  # noqa: F821
drift_df.write.mode("append").insertInto(PRICES)
print(len(rows), "drift-demo prices appended. Now run quote_ai_ml_features, then quote_ai_ml_score.")

# COMMAND ----------

# 2. After both jobs: last night's scores against tonight's, the same check the monitor runs (PSI > 0.2 = drift)
RUNS = f"SELECT DISTINCT scored_at FROM {SCORES} ORDER BY scored_at DESC LIMIT 2"
latest, previous = [r.scored_at for r in spark.sql(RUNS).collect()]  # noqa: F821


def scores_at(t):
    return [r.asDict() for r in spark.table(SCORES).filter(f"scored_at = '{t}'").collect()]  # noqa: F821


for column, result in monitor.drift(scores_at(previous), scores_at(latest)).items():
    print(f"{column:32} PSI {result['psi']:.3f}  {'DRIFT' if result['drift'] else 'ok'}")
