"""The win model's endpoint in CD (AI-14).

  --champion-version --model CAT.SCHEMA.win_model
      print the version behind @champion (or 1 before the first registration), for
      databricks bundle deploy --var quote_ai_win_model_version=...: a deploy keeps the endpoint on the champion
      instead of resetting it to a version written by hand
  --query --endpoint quote_ai_win_model_<target>
      the smoke test after a deploy to dev-shared: one what-if query (Lakeside, Glove A size M, $7.60 a box) must
      return a probability between 0 and 1

Needs databricks-sdk and the deploying identity's credentials (DATABRICKS_HOST, client ID and secret).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def champion_version(model: str) -> str:
    from databricks.sdk import WorkspaceClient
    from databricks.sdk.errors import NotFound
    try:
        return str(WorkspaceClient().registered_models.get_by_alias(model, "champion").version)
    except NotFound:
        return "1"


def smoke(endpoint: str) -> float:
    from quote_ai.ml import score
    lines = [{"sku_id": "GLOVE-A-M", "quantity": 400, "list_price": 8.60, "unit_price": 8.60}]
    rows = score.what_if_rows("smoke", "Lakeside Regional Clinic", lines, [7.60])
    (r,) = score.win_chances(rows, score.query_endpoint(endpoint))
    return r["win_chance"]


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--champion-version", action="store_true")
    p.add_argument("--model")
    p.add_argument("--query", action="store_true")
    p.add_argument("--endpoint")
    a = p.parse_args(argv)
    if a.champion_version:
        print(champion_version(a.model))
    if a.query:
        print(f"{a.endpoint}: Lakeside at $7.60 -> {smoke(a.endpoint):.0%} (simulation, demo data)")


if __name__ == "__main__":
    main()
