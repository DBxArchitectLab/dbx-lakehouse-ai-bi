"""The bundle resources for ingestion, generated from config/ingest/sources.yml.

  resources/quote_ai_ingest.schemas.yml         the bronze and ops schemas, and the landing volumes
  resources/quote_ai_ingest_bronze.pipeline.yml  the bronze pipeline: every file table of every enabled file source
  resources/quote_ai_ingest_<schedule>.job.yml   one job per schedule: setup, land, files, record, api, checks

Run scripts/gen_ingest_resources.py after changing the config; a test fails the pull request when the files in the
repo don't match the config. The YAML stays in the repo, so the review shows exactly what will be deployed.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from quote_ai.ingest.config import IngestConfig

HEADER = ("# GENERATED from config/ingest/sources.yml by scripts/gen_ingest_resources.py: do not edit by hand.\n"
          "# A test fails the pull request when this file and the config disagree.\n")
PIPELINE_KEY = "quote_ai_ingest_bronze"
SCRIPT = "../src/jobs/quote_ai_ingest.py"
CONFIG = "${workspace.file_path}/config/ingest/sources.yml"
DEPENDENCIES = ["pydantic>=2.7", "pyyaml>=6", "requests>=2.31"]
CRON = {"daily": "0 0 6 * * ?", "weekly": "0 0 6 ? * MON"}
SCHEDULE_ORDER = ["once", "daily", "weekly", "on_demand"]


def _schemas_arg(cfg: IngestConfig) -> str:
    return ",".join(f"{s}=${{resources.schemas.{s}.name}}" for s in [*cfg.schemas(), "ops"])


def schemas_yaml(cfg: IngestConfig) -> dict:
    schemas = {s: {"catalog_name": "${var.catalog}", "name": s,
                   "comment": f"QuoteAI ingestion: {s} (written only by the ingestion jobs)"} for s in cfg.schemas()}
    schemas["ops"] = {"catalog_name": "${var.catalog}", "name": "ops",
                      "comment": "QuoteAI operations: ingestion log, file manifest, config history, audit view"}
    volumes = {}
    for v in cfg.volumes():
        schema, name = v.split(".")
        volumes[f"{schema}_{name}"] = {"catalog_name": "${var.catalog}",
                                       "schema_name": f"${{resources.schemas.{schema}.name}}", "name": name,
                                       "volume_type": "MANAGED",
                                       "comment": "Landing zone: release files exactly as published; never edited"}
    return {"resources": {"schemas": schemas, "volumes": volumes}}


def pipeline_yaml(cfg: IngestConfig) -> dict:
    return {"resources": {"pipelines": {PIPELINE_KEY: {
        "name": PIPELINE_KEY,
        "catalog": "${var.catalog}",
        "schema": "${resources.schemas.ops.name}",
        "serverless": True,
        "continuous": False,
        "libraries": [{"file": {"path": "../src/quote_ai/ingest/bronze_pipeline.py"}}],
        "environment": {"dependencies": DEPENDENCIES[:2]},
        "configuration": {
            "quote_ai.src_path": "${workspace.file_path}/src",
            "quote_ai.config_path": CONFIG,
            "quote_ai.catalog": "${var.catalog}",
            "quote_ai.schemas": _schemas_arg(cfg),
        },
    }}}}


def _task(key: str, command: list[str], cfg: IngestConfig, depends: list[str] | None = None,
          run_if: str | None = None) -> dict:
    t: dict = {"task_key": key}
    if depends:
        t["depends_on"] = [{"task_key": d} for d in depends]
    if run_if:
        t["run_if"] = run_if
    t["environment_key"] = "default"
    t["spark_python_task"] = {"python_file": SCRIPT, "parameters": [
        *command, "--config", CONFIG, "--catalog", "${var.catalog}", "--schemas", _schemas_arg(cfg),
        "--job-id", "{{job.id}}", "--job-run-id", "{{job.run_id}}", "--bundle-target", "${bundle.target}",
        "--git-commit", "${bundle.git.commit}"]}
    return t


def job(cfg: IngestConfig, schedule: str) -> dict:
    files = cfg.enabled(type="file", schedule=schedule)
    apis = cfg.enabled(type="api", schedule=schedule)
    tasks = [_task("setup", ["setup"], cfg)]
    last = "setup"
    if files:
        land = ["land", "--schedule", schedule]
        if schedule == "once":
            land += ["--release-date", "{{job.parameters.release_date}}"]
        tasks.append(_task("land", land, cfg, [last]))
        tasks.append({"task_key": "files", "depends_on": [{"task_key": "land"}],
                      "pipeline_task": {"pipeline_id": f"${{resources.pipelines.{PIPELINE_KEY}.id}}"}})
        tasks.append(_task("record", ["record", "--schedule", schedule, "--pipeline-state",
                                      "{{tasks.files.result_state}}"], cfg, ["files"], run_if="ALL_DONE"))
        last = "record"
    for s in apis:
        tasks.append(_task(f"api_{s.name}", ["api", "--source", s.name], cfg, [last]))
        last = f"api_{s.name}"
    tasks.append(_task("checks", ["checks"], cfg, [last]))
    sources = ", ".join(s.name for s in [*files, *apis])
    j: dict = {"name": f"quote_ai_ingest_{schedule}",
               "description": f"QuoteAI ingestion, {schedule}: {sources}",
               "max_concurrent_runs": 1,
               "tags": {"app": "quote_ai", "part": "ingestion"}}
    if schedule == "once":
        j["parameters"] = [{"name": "release_date", "default": ""}]
    if schedule in CRON:
        j["schedule"] = {"quartz_cron_expression": CRON[schedule], "timezone_id": "UTC"}
    j["tasks"] = tasks
    j["environments"] = [{"environment_key": "default",
                          "spec": {"environment_version": "2", "dependencies": DEPENDENCIES}}]
    return j


def render(cfg: IngestConfig) -> dict[str, str]:
    """File name (under resources/) -> content. One job per file, as the bundle recommends."""
    dump = lambda d: HEADER + yaml.safe_dump(d, sort_keys=False, width=120, allow_unicode=True)  # noqa: E731
    out = {"quote_ai_ingest.schemas.yml": dump(schemas_yaml(cfg)),
           f"{PIPELINE_KEY}.pipeline.yml": dump(pipeline_yaml(cfg))}
    present = {s.schedule for s in cfg.enabled()}
    for s in SCHEDULE_ORDER:
        if s in present:
            out[f"quote_ai_ingest_{s}.job.yml"] = dump({"resources": {"jobs": {f"quote_ai_ingest_{s}": job(cfg, s)}}})
    return out


def stale_files(cfg: IngestConfig, resources_dir: Path) -> list[str]:
    """Generated files that are missing, don't match the config (line endings ignored: Git may convert them), or are
    left over from an entry the config no longer has."""
    expected = render(cfg)
    out = [name for name, text in expected.items()
           if not (resources_dir / name).exists()
           or (resources_dir / name).read_text(encoding="utf-8").replace("\r\n", "\n") != text]
    out += sorted(p.name for p in resources_dir.glob("quote_ai_ingest*.yml") if p.name not in expected)
    return out


def write(cfg: IngestConfig, resources_dir: Path) -> list[Path]:
    """Write the generated files and remove generated files the config no longer produces."""
    expected = render(cfg)
    for p in resources_dir.glob("quote_ai_ingest*.yml"):
        if p.name not in expected and p.read_text(encoding="utf-8").startswith(HEADER[:20]):
            p.unlink()
    out = []
    for name, text in render(cfg).items():
        p = resources_dir / name
        p.write_text(text, encoding="utf-8", newline="\n")
        out.append(p)
    return out
