# Databricks notebook source
# MAGIC %md
# MAGIC # AI-12: from quote history to ML features, and a leak caught live
# MAGIC **Synthetic demo data.** The quote history is generated with outcomes from a documented, planted signal
# MAGIC (docs/quote_ai/ai-12-ml-features/README.md): an educational simulation, not evidence about real sales.
# MAGIC 1. Lakeside's quote history: price offered, what it paid at the time, lead time, outcome
# MAGIC 2. The label: won = 1, lost or expired = 0, cancelled dropped (the win-rate metric of AI-10, ADR 0003)
# MAGIC 3. The trap: final_negotiated_price as a feature, a quick model, a random split
# MAGIC 4. A time split, the leak kept: still suspicious. Then the leak removed: an honest score
# MAGIC 5. Point in time: Lakeside's win_rate_12m on two quote dates; a plain join gives both today's value
# MAGIC
# MAGIC Run quote_ai_ml_features first (`databricks bundle run quote_ai_ml_features -t dev`).

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

from quote_ai.ml import features as F  # noqa: E402
from quote_ai.ml import quick  # noqa: E402

dbutils.widgets.text("catalog", "dbxarchitectlab_dev")  # noqa: F821
dbutils.widgets.text("prefix", "")  # noqa: F821  dev_<user>_ in a personal dev copy, blank in dev-shared and prod
CAT, PRE = dbutils.widgets.get("catalog"), dbutils.widgets.get("prefix")  # noqa: F821
SALES, ML = f"{CAT}.{PRE}quote_ai_sales", f"{CAT}.{PRE}quote_ai_ml"

# COMMAND ----------

# MAGIC %md ## 1. Lakeside's quote history (synthetic)

# COMMAND ----------

HISTORY = f"""
  SELECT quote_id, sent_at, sku_id, quantity, unit_price, customer_current_price, lead_time_days, in_stock, outcome
  FROM {SALES}.quote_history WHERE account_id = 'ACC-001' ORDER BY sent_at DESC LIMIT 50"""
display(spark.sql(HISTORY))  # noqa: F821

# COMMAND ----------

# MAGIC %md ## 2. The label, the same as the win-rate metric

# COMMAND ----------

LABEL = f"""
  SELECT outcome, CASE WHEN outcome = 'WON' THEN 1 WHEN outcome IN {F.DECIDED} THEN 0 END AS won, count(*) AS quotes
  FROM {SALES}.quote_fact GROUP BY ALL ORDER BY outcome"""
display(spark.sql(LABEL))  # noqa: F821

# COMMAND ----------

# MAGIC %md ## 3 and 4. The trap: a feature that knows the future

# COMMAND ----------

training = [r.asDict() for r in spark.table(f"{ML}.win_training_set").collect()]  # noqa: F821
history = spark.table(f"{SALES}.quote_history").select("quote_id", "final_negotiated_price")  # noqa: F821
after = {r.quote_id: r.final_negotiated_price for r in history.collect()}
leaky = [r | {"final_negotiated_price": after.get(r["quote_id"])} for r in training]
cols = list(F.FEATURES)

a, b = quick.random_split(leaky)
print(f"with final_negotiated_price, random split: test AUC {quick.score(a, b, cols + ['final_negotiated_price']):.3f}")
tr, _, te = F.time_split(leaky)
print(f"with final_negotiated_price, time split:   test AUC {quick.score(tr, te, cols + ['final_negotiated_price']):.3f}")
print("refused:", F.check_training_set(leaky[:1]))
tr, _, te = F.time_split(training)
print(f"without the leak, time split (honest):      test AUC {quick.score(tr, te, cols):.3f}")

# COMMAND ----------

# MAGIC %md ## 5. Point in time: two quote dates, two values

# COMMAND ----------

POINT_IN_TIME = f"""
  WITH q AS (SELECT quote_id, sent_at FROM {ML}.quote_labels
             WHERE account_id = 'ACC-001' AND quote_id IN ('QH-02875', 'QH-03716'))
  SELECT q.quote_id, q.sent_at,
         (SELECT max_by(win_rate_12m, as_of) FROM {ML}.account_features f
          WHERE f.account_id = 'ACC-001' AND f.as_of <= q.sent_at) AS point_in_time,
         (SELECT max_by(win_rate_12m, as_of) FROM {ML}.account_features f
          WHERE f.account_id = 'ACC-001') AS plain_join_today
  FROM q ORDER BY q.sent_at"""
display(spark.sql(POINT_IN_TIME))  # noqa: F821

# COMMAND ----------

# MAGIC %md Next: Catalog Explorer > the quote_ai_ml schema > account_features > Lineage, into win_training_set.
