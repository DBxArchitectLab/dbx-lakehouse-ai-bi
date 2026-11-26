"""The release manifest (AI-25): release/release.yml names QuoteAI 1.0 as a set of versions, and CI refuses one that
cannot be promoted. In the workspace (QUOTE_AI_RELEASE_CATALOG set), every model and prompt must also exist in the
target catalog; locally the same check runs on an in-memory registry."""
import os
from pathlib import Path

import pytest
import yaml

from quote_ai.llm import prompts, rag
from quote_ai.ops import promote
from quote_ai.search import index

ROOT = Path(__file__).resolve().parents[2]


def manifest():
    return promote.load_manifest()


def test_the_manifest_can_be_promoted():
    assert promote.validate(manifest()) == []


def test_versions_are_whole_numbers():
    m = manifest()
    for section in ("models", "prompts"):
        assert m[section], section
        for name, v in m[section].items():
            assert isinstance(v, int) and not isinstance(v, bool) and v > 0, f"{section}.{name}"


def test_the_evaluation_package_version_matches_v1_version():
    want = (ROOT / "evaluation" / "quote_ai" / "v1" / "VERSION").read_text(encoding="utf-8").strip()
    assert str(manifest()["evaluation_package"]) == want


def test_every_prompt_is_one_the_code_reads_by_alias():
    assert promote.PROMPTS == {"extraction": prompts.NAME, "comparison": rag.PROMPT_NAME}
    assert set(manifest()["prompts"]) <= set(promote.PROMPTS)
    assert set(manifest()["models"]) <= set(promote.MODELS)


def test_the_index_and_its_embedding_service_are_in_the_bundle():
    idx = manifest()["index"]
    assert set(idx) == {index.SKU["index"]}
    services = yaml.safe_load((ROOT / "resources" / "quote_ai" / "quote_ai_embed.model_service.yml").read_text(
        encoding="utf-8"))["resources"]["model_services"]
    assert idx[index.SKU["index"]]["embedding_endpoint"] in services


@pytest.mark.parametrize("change, error", [
    ({"models": {"win_model": "7"}}, "whole number"),
    ({"models": {"win_model": True}}, "whole number"),
    ({"prompts": {"extraction": 0}}, "whole number"),
    ({"models": {"loss_model": 1}}, "unknown model"),
    ({"prompts": {"summary": 1}}, "unknown prompt"),
    ({"evaluation_package": "1.1.0"}, "VERSION"),
])
def test_a_bad_manifest_is_refused(change, error):
    m = manifest() | change
    assert any(error in e for e in promote.validate(m))


def test_a_manifest_without_models_is_refused():
    m = {k: v for k, v in manifest().items() if k != "models"}
    assert "missing models" in promote.validate(m)


def test_every_version_exists_in_the_target_catalog_locally():
    names = promote.target_names("dbxarchitectlab_prod")
    m = manifest()
    have = {(mv.kind, mv.name): {"versions": {mv.version}, "aliases": {}} for mv in promote.plan(m, names)}
    assert promote.check(m, names, promote.InMemoryRegistry(have)) == []
    first = next(iter(have))
    one_short = dict(have) | {first: {"versions": set(), "aliases": {}}}
    assert len(promote.check(m, names, promote.InMemoryRegistry(one_short))) == 1


@pytest.mark.skipif(not os.environ.get("QUOTE_AI_RELEASE_CATALOG"),
                    reason="needs the workspace (QUOTE_AI_RELEASE_CATALOG)")
def test_every_version_exists_in_the_workspace_catalog():
    names = promote.target_names(os.environ["QUOTE_AI_RELEASE_CATALOG"], os.environ.get("QUOTE_AI_RELEASE_SCHEMAS"))
    assert promote.check(manifest(), names, promote.MlflowRegistry()) == []


def _jobs(name):
    return yaml.safe_load((ROOT / "resources" / "quote_ai" / name).read_text(encoding="utf-8"))["resources"]["jobs"]


def test_the_release_jobs_are_in_the_bundle():
    for f, key in (("quote_ai_promote.job.yml", "quote_ai_promote"),
                   ("quote_ai_rules_setup.job.yml", "quote_ai_rules_setup"),
                   ("quote_ai_smoke_test.job.yml", "quote_ai_smoke_test")):
        job = _jobs(f)[key]
        assert job["name"] == key and job["tags"]["app"] == "quote_ai"
    tasks = [t["task_key"] for t in _jobs("quote_ai_promote.job.yml")["quote_ai_promote"]["tasks"]]
    assert tasks == ["check", "promote"]
    rules = _jobs("quote_ai_rules_setup.job.yml")["quote_ai_rules_setup"]["tasks"]
    assert rules[-1]["run_job_task"]["job_id"] == "${resources.jobs.quote_ai_security_setup.id}"


def test_cd_prod_runs_the_gates_in_order_after_the_approval():
    cd = yaml.safe_load((ROOT / ".github" / "workflows" / "cd.yml").read_text(encoding="utf-8"))
    prod = cd["jobs"]["prod"]
    assert prod["environment"] == "prod"
    runs = [s.get("run", "") for s in prod["steps"]]
    order = ["build_app.py", "bundle validate -t prod", "quote_ai.ops.promote --catalog dbxarchitectlab_prod --check",
             "bundle deploy -t prod", "quote_ai_rules_setup", "quote_ai_promote -t prod", "quote_ai_app -t prod",
             "quote_ai_smoke_test -t prod"]
    at = [next(i for i, r in enumerate(runs) if o in r) for o in order]
    assert at == sorted(at), order
    deploy = next(r for r in runs if "bundle deploy -t prod" in r)
    assert "$WIN_MODEL_VERSION" in deploy
    assert any("--get models.win_model" in r for r in runs)       # the endpoint serves the manifest's version
    on = cd.get("on") or cd.get(True)                             # PyYAML reads the key `on` as True
    assert "rollback_to" in on["workflow_dispatch"]["inputs"]
