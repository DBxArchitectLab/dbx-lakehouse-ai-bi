"""Create or update the DBxLab sales analyst (a Genie Agent) from sales_analyst.yml (AI-11). Run by the quote_ai_genie
job after the comments (src/quote_ai/semantic/comments.sql); the YAML is the source of truth, the agent a copy.

  load_spec(catalog, schemas)   the YAML with the target's catalog.schema names filled in
  serialized_space(spec, cases) the agent as the Genie API stores it: tables, instructions, example SQL, sample
                                questions and the benchmark questions with their expected SQL
  deploy(...)                   find the agent by title (or create it), update it, share it with the Sales groups

  python src/quote_ai/semantic/genie/deploy_genie.py --catalog dbxarchitectlab_dev --warehouse-id <id> \
      [--schemas quote_ai_sales=dev_me_quote_ai_sales,...] [--groups DBxLab_Sales_Reps,DBxLab_Sales_Managers]

Verify before recording: the serialized_space format (GET a space with include_serialized_space=true and compare),
whether ontology terms and benchmarks can be set through it, and the permissions object type for a Genie space. Any
setting the API can't carry is a manual step in docs/quote_ai/ai-11-genie-agents/README.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parents[2]
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import yaml  # noqa: E402

from quote_ai.storage import sql_files  # noqa: E402

SPEC = HERE / "sales_analyst.yml"
CASES = SRC.parent / "evaluation" / "quote_ai" / "v1" / "genie" / "cases.jsonl"


def load_spec(catalog: str, schemas: dict[str, str] | None = None, path: Path = SPEC) -> dict:
    names = sql_files.names_for(catalog, schemas or {})
    return yaml.safe_load(sql_files.render(path.read_text(encoding="utf-8"), **names))


def load_cases(catalog: str, schemas: dict[str, str] | None = None, path: Path = CASES) -> list[dict]:
    names = sql_files.names_for(catalog, schemas or {})
    cases = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [c | {"expected_sql": sql_files.render(c["expected_sql"], **names)} for c in cases]


def _id(*parts: str) -> str:
    """Stable 32-hex IDs, so a redeploy updates the same items instead of adding new ones."""
    return uuid.uuid5(uuid.NAMESPACE_URL, "quote_ai/genie/" + "/".join(parts)).hex


def _lines(text: str) -> list[str]:
    return [l + "\n" for l in text.strip().splitlines()]


def serialized_space(spec: dict, cases: list[dict]) -> str:
    """The agent as JSON for the Genie API (one adapter: the only place that knows the format)."""
    instructions = "\n\n".join(f"[{k}]\n{v.strip()}" for k, v in spec["instructions"].items())
    terms = "\n".join(f"- {t['term']}: {t['maps_to']} (also: {', '.join(t.get('synonyms', []))})"
                      for t in spec.get("ontology", []))
    space = {
        "version": 1,
        "config": {"sample_questions": [{"id": _id("sample", q), "question": [q]}
                                        for q in spec.get("sample_questions", [])]},
        "data_sources": {"tables": sorted(({"identifier": t["name"]} for t in spec["tables"]),
                                          key=lambda t: t["identifier"])},
        "instructions": {
            "text_instructions": [{"id": _id("instructions"),
                                   "content": _lines(instructions + "\n\nBusiness terms:\n" + terms)}],
            "example_question_sqls": [{"id": _id("example", e["question"]), "question": [e["question"]],
                                       "sql": _lines(e["sql"])} for e in spec.get("example_queries", [])],
        },
        "benchmarks": {"questions": [{"id": _id("benchmark", c["id"]), "question": [c["question"]],
                                      "answer": [{"format": "SQL", "content": _lines(c["expected_sql"])}]}
                                     for c in cases]},
    }
    return json.dumps(space)


def deploy(catalog: str, warehouse_id: str, schemas: dict[str, str] | None = None, groups: list[str] = (),
           client=None) -> str:
    """Create or update the agent; returns its space ID (set it as the bundle variable quote_ai_genie_space_id)."""
    if client is None:
        from databricks.sdk import WorkspaceClient
        client = WorkspaceClient()
    spec = load_spec(catalog, schemas)
    body = serialized_space(spec, load_cases(catalog, schemas))
    existing = next((s for s in (client.genie.list_spaces().spaces or []) if s.title == spec["title"]), None)
    if existing is None:
        space = client.genie.create_space(warehouse_id=warehouse_id, serialized_space=body, title=spec["title"],
                                          description=spec["description"])
    else:
        space = client.genie.update_space(existing.space_id, serialized_space=body, title=spec["title"],
                                          description=spec["description"], warehouse_id=warehouse_id)
    if groups:
        from databricks.sdk.service.iam import AccessControlRequest, PermissionLevel
        client.permissions.set("genie", space.space_id, access_control_list=[
            AccessControlRequest(group_name=g, permission_level=PermissionLevel.CAN_RUN) for g in groups])
    return space.space_id


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--catalog", required=True)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--schemas", default="")
    p.add_argument("--groups", default="")
    a = p.parse_args(argv)
    schemas = dict(x.split("=", 1) for x in a.schemas.split(",") if x)
    space_id = deploy(a.catalog, a.warehouse_id, schemas, [g for g in a.groups.split(",") if g])
    print(f"Genie Agent {space_id}: set quote_ai_genie_space_id={space_id} for the app's Ask the data panel")


if __name__ == "__main__":
    main()
