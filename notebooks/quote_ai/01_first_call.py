# Databricks notebook source
# MAGIC %md
# MAGIC # AI-01: the first model call
# MAGIC The Lakeside request, sent to a model with no DBxLab data at all. Whatever it answers, confident or careful,
# MAGIC nothing in it is verified against DBxLab's records: the model can't see our catalog, stock or prices.
# MAGIC The tokens and seconds printed below are the baseline AI-04 compares models against.
# MAGIC
# MAGIC Today the call goes straight to a pay-per-token Foundation Model API endpoint; from AI-06 it goes through
# MAGIC Unity Gateway. Run it on serverless compute in the dev workspace.

# COMMAND ----------

# MAGIC %pip install openai --quiet

# COMMAND ----------

import os
import sys

# The bundle's src folder, so quote_ai imports (this notebook is at <bundle>/notebooks/quote_ai/).
sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

from quote_ai.llm.client import chat  # noqa: E402

dbutils.widgets.text("endpoint", "databricks-meta-llama-3-3-70b-instruct")  # noqa: F821  any chat endpoint
ep = dbutils.widgets.get("endpoint")  # noqa: F821

LAKESIDE = ("Lakeside Regional Clinic needs 400 boxes of powder-free nitrile exam gloves, chemo-tested, "
            "latex-free, sizes S to XL. What can we offer, and how does it compare on quality?")

# COMMAND ----------

r = chat(LAKESIDE, endpoint=ep)
print(r.text)
print()
print(f"endpoint {ep}: {r.usage.prompt_tokens} prompt + {r.usage.completion_tokens} completion = "
      f"{r.usage.total_tokens} tokens, {r.seconds:.1f}s")
# Confident or careful: unverified either way. Note the tokens and seconds: the baseline for AI-04.
