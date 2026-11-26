# Databricks notebook source
# MAGIC %md
# MAGIC # AI-04: which model reads a glove description best?
# MAGIC Three endpoints, the same prompt, the same 30 hand-labelled gloves (evaluation/quote_ai/v1/product_facts).
# MAGIC One MLflow run per endpoint: accuracy, hallucinations, tokens and latency. Cost per 1,000 gloves comes from the
# MAGIC measured tokens and the pay-per-token prices you enter below (check the pricing page: prices change).
# MAGIC Write the decision in docs/quote_ai/decisions/0001-model-per-job.md.

# COMMAND ----------

# MAGIC %pip install openai mlflow --quiet

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

import mlflow  # noqa: E402

from quote_ai.llm import compare_models as cm  # noqa: E402

dbutils.widgets.text("endpoints", "databricks-meta-llama-3-3-70b-instruct,databricks-llama-4-maverick,"  # noqa: F821
                     "databricks-claude-sonnet-4")
dbutils.widgets.text("prices_usd_per_m", "")  # noqa: F821  endpoint=in/out,...  e.g. databricks-x=0.5/1.5
dbutils.widgets.text("experiment", "")  # noqa: F821  blank: this notebook's experiment
endpoints = [e.strip() for e in dbutils.widgets.get("endpoints").split(",") if e.strip()]  # noqa: F821
prices = {k: tuple(float(x) for x in v.split("/")) for k, v in
          (p.split("=") for p in dbutils.widgets.get("prices_usd_per_m").split(",") if "=" in p)}  # noqa: F821
if dbutils.widgets.get("experiment"):  # noqa: F821
    mlflow.set_experiment(dbutils.widgets.get("experiment"))  # noqa: F821

cases = cm.load_cases(os.path.abspath(os.path.join(os.getcwd(), "..", "..", "evaluation", "quote_ai", "v1",
                                                   "product_facts", "cases.jsonl")))
print(len(cases), "hand-labelled gloves;", "fields:", cm.FIELDS)

# COMMAND ----------

results = {}
for ep in endpoints:
    results[ep] = cm.run(ep, cases)
    m = results[ep]["metrics"]
    if ep in prices:
        m["usd_per_1000_gloves"] = cm.cost_per_1000(m["tokens_in"], m["tokens_out"], len(cases), *prices[ep])
    print(ep, m)

# COMMAND ----------

# MAGIC %md
# MAGIC Compare the runs in the MLflow experiment (accuracy, tokens_in, tokens_out, latency_avg_s). Then the question the
# MAGIC description doesn't answer: is it chemo-tested? A model that answers "yes" for a glove whose text never says so
# MAGIC is guessing; that is why AI-08 stores AI values as unverified.

# COMMAND ----------

from quote_ai.llm.client import chat  # noqa: E402

desc = cases[0]["description"]
for ep in endpoints:
    print(ep, "->", chat(f"{desc}\n\nIs this glove tested for use with chemotherapy drugs? Answer yes, no or not "
                         f"stated.", endpoint=ep).text)
