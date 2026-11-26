# Databricks notebook source
# MAGIC %md
# MAGIC # AI-06: the three calls that must be stopped
# MAGIC Every model call now goes through a Unity Gateway model service. This notebook runs the Model access suite
# MAGIC (evaluation/quote_ai/v1/model_access) against quote_ai_chat_<target>:
# MAGIC 1. no grant: run this cell as a user outside DBxLab_Sales_Reps -> 403
# MAGIC 2. over the limit: 25 calls in a minute -> 429 after 20
# MAGIC 3. blocked content: an email address and a phone number -> blocked by the PII service policy
# MAGIC and one allowed call. Then the usage: who called, which service, how many tokens.

# COMMAND ----------

# MAGIC %pip install openai --quiet

# COMMAND ----------

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.getcwd(), "..", "..", "src")))

dbutils.widgets.text("target", "dev")  # noqa: F821  dev, dev_shared or prod
os.environ["BUNDLE_TARGET"] = dbutils.widgets.get("target")  # noqa: F821

from quote_ai.llm import endpoints  # noqa: E402
from quote_ai.llm.client import chat  # noqa: E402
from quote_ai.llm.gateway_checks import load_cases, outcome, verdict  # noqa: E402

CHAT = endpoints.model(endpoints.CHAT)
me = spark.sql("SELECT current_user()").first()[0]  # noqa: F821
print("calling", CHAT, "as", me)


def call(prompt):
    try:
        return chat(prompt, endpoint=CHAT)
    except Exception as e:  # noqa: BLE001  the refusal is the result
        return e


cases = {c["id"]: c for c in load_cases()}

# COMMAND ----------

# The allowed call (as a rep), and call 1, no grant (run this cell again as a user outside the reps group).
r = call(cases["allowed"]["prompt"])
print(outcome(r), "-", getattr(r, "text", r))

# COMMAND ----------

# Call 2, over the limit: 25 calls in a loop, printing the error name for each. Expect 20 answered, then 429.
results = []
for i in range(cases["over-the-limit"]["calls"]):
    r = call("ping")
    results.append(outcome(r))
    print(i + 1, results[-1], "" if results[-1] == "allowed" else f"{type(r).__name__}: {str(r)[:80]}")
print("over-the-limit passes:", verdict(cases["over-the-limit"], results))

# COMMAND ----------

# Call 3, blocked content: personal contact details are stopped by the service policy.
r = call(cases["blocked-content"]["prompt"])
print(outcome(r), "-", str(getattr(r, "text", r))[:200])

# COMMAND ----------

# MAGIC %md
# MAGIC Usage per identity and service. Verify the system table and column names before recording.

# COMMAND ----------

display(spark.sql("""
    SELECT requester, model_service_name, count(*) AS calls,
           sum(input_token_count) AS tokens_in, sum(output_token_count) AS tokens_out
    FROM system.ai_gateway.usage
    WHERE model_service_name LIKE 'quote_ai_%'
    GROUP BY ALL ORDER BY tokens_in DESC"""))  # noqa: F821
