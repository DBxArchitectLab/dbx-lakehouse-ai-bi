# Databricks notebook source
# MAGIC %md
# MAGIC # AI-15: search finds candidates; the rules decide
# MAGIC **Demo data.** Three searches on the SKU index (Databricks AI Search, formerly Vector Search), then the
# MAGIC eligibility engine as a Unity Catalog function. Run `quote_ai_search_index` first.
# MAGIC 1. By meaning: "gloves for handling chemotherapy drugs, no latex": nitrile, neoprene, polyisoprene on top, no vinyl.
# MAGIC 2. By exact catalog number: vector only returns a near miss; hybrid returns the exact SKU first.
# MAGIC 3. With a size filter: size M only, and the discontinued glove (Glove M) disappears.

# COMMAND ----------

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

from quote_ai.search import index, retriever  # noqa: E402

dbutils.widgets.text("catalog", "dbxarchitectlab_dev")  # noqa: F821
dbutils.widgets.text("prefix", "")  # noqa: F821  dev_<user>_ in a personal dev copy
CAT, PRE = dbutils.widgets.get("catalog"), dbutils.widgets.get("prefix")  # noqa: F821
AI, PRODUCT = f"{CAT}.{PRE}quote_ai_ai", f"{CAT}.{PRE}quote_ai_product"
idx = index.get_index(AI, "sku")


def show(hits, n=8):
    for h in hits[:n]:
        print(f"{h['score']:.4f}  {h['catalog_number']:<16} {h['product_id']:<8} {h['search_text'][:90]}")

# COMMAND ----------

# 1. By meaning
show(retriever.search_candidates(idx, "gloves for handling chemotherapy drugs, no latex", k=20))

# COMMAND ----------

# 2. An exact catalog number: vector only, then hybrid
show(retriever.search_candidates(idx, "DBX-NC-M-100", k=5, query_type="ANN", in_distribution=None), 3)
print("---")
show(retriever.search_candidates(idx, "DBX-NC-M-100", k=5, query_type="HYBRID", in_distribution=None), 3)

# COMMAND ----------

# 3. Size M only; the distribution filter narrows candidates (the engine checks distribution again, from evidence)
show(retriever.search_candidates(idx, "nitrile exam chemo-tested", ["M"], k=20, in_distribution=None), 12)
print("--- in distribution only")
show(retriever.search_candidates(idx, "nitrile exam chemo-tested", ["M"], k=20), 12)

# COMMAND ----------

# 4. The rules, as a Unity Catalog function: Glove A eligible, Glove B needs evidence (chemo only claimed), E excluded
from quote_ai.domain.models import Requirements  # noqa: E402

r2 = Requirements(version=2, material="nitrile", powder_free=True, intended_use="exam", latex_free=True,
                  chemo_tested=True, sizes=["S", "M", "L", "XL"], quantity=400, unit="box", gloves_per_unit=100,
                  quantity_by_size={"S": 60, "M": 140, "L": 140, "XL": 60}, delivery_days=10, target_unit_price=8.0)
for sku in ("GLOVE-A-M", "GLOVE-B-M", "GLOVE-E-M"):
    out = json.loads(spark.sql(f"SELECT {AI}.check_eligibility(:s, :r) AS r",  # noqa: F821
                               args={"s": sku, "r": r2.model_dump_json()}).first().r)
    print(sku, out["status"], [(c["requirement"], c["result"]) for c in out["checks"] if c["result"] != "verified_match"])
