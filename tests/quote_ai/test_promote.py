"""Alias promotion (AI-25): only pointers move, only in the target catalog, and a second run changes nothing."""
import pytest

from quote_ai.ops import promote

PROD = promote.target_names("dbxarchitectlab_prod")
MANIFEST = {"quote_ai": "1.0.0", "evaluation_package": promote.package_version(),
            "models": {"win_model": 7}, "prompts": {"extraction": 5, "comparison": 4}}
PREVIOUS = MANIFEST | {"quote_ai": "0.9.0", "models": {"win_model": 6}, "prompts": {"extraction": 4, "comparison": 4}}


def registry():
    return promote.InMemoryRegistry({
        ("model", "dbxarchitectlab_prod.quote_ai_ml.win_model"): {"versions": {5, 6, 7}, "aliases": {"champion": 6}},
        ("prompt", "dbxarchitectlab_prod.quote_ai_ai.extract_requirements"): {"versions": {3, 4, 5},
                                                                              "aliases": {"champion": 4}},
        ("prompt", "dbxarchitectlab_prod.quote_ai_ai.compare_explain"): {"versions": {1, 4},
                                                                         "aliases": {"champion": 4}},
    })


def test_promotion_moves_the_champion_aliases_to_the_manifest():
    reg = registry()
    rows = promote.promote(MANIFEST, PROD, reg)
    got = {r["name"].split(".")[-1]: (r["from"], r["version"], r["status"]) for r in rows}
    assert got == {"win_model": (6, 7, "moved"), "extract_requirements": (4, 5, "moved"),
                   "compare_explain": (4, 4, "unchanged")}
    assert len(reg.writes) == 2 and all(w[2] == "champion" for w in reg.writes)


def test_promotion_is_idempotent():
    reg = registry()
    promote.promote(MANIFEST, PROD, reg)
    writes = list(reg.writes)
    again = promote.promote(MANIFEST, PROD, reg)
    assert reg.writes == writes and {r["status"] for r in again} == {"unchanged"}


def test_it_never_moves_an_alias_in_another_catalog():
    reg = registry()
    names = dict(PROD) | {"ai": "dbxarchitectlab_dev.quote_ai_ai"}      # a schema resolved to the wrong catalog
    with pytest.raises(PermissionError, match="outside the target catalog"):
        promote.promote(MANIFEST, names, reg)
    assert reg.writes == []                                            # checked before anything moved


def test_rollback_is_the_previous_manifest_promoted():
    reg = registry()
    promote.promote(MANIFEST, PROD, reg)
    rows = promote.promote(PREVIOUS, PROD, reg)
    assert {r["name"].split(".")[-1]: r["version"] for r in rows if r["status"] == "moved"} == {
        "win_model": 6, "extract_requirements": 4}
    assert reg.get_alias("model", "dbxarchitectlab_prod.quote_ai_ml.win_model", "champion") == 6


def test_a_dry_run_writes_nothing_and_an_invalid_manifest_moves_nothing():
    reg = registry()
    assert {r["status"] for r in promote.promote(MANIFEST, PROD, reg, dry_run=True)} == {"would_move", "unchanged"}
    with pytest.raises(ValueError, match="whole number"):
        promote.promote(MANIFEST | {"models": {"win_model": "latest"}}, PROD, reg)
    assert reg.writes == []


def test_check_names_the_missing_versions():
    missing = promote.check(MANIFEST | {"models": {"win_model": 8}}, PROD, registry())
    assert missing == ["model dbxarchitectlab_prod.quote_ai_ml.win_model version 8"]


def test_the_plan_uses_the_bundles_schema_names():
    names = promote.target_names("dbxarchitectlab_dev", "quote_ai_ml=dev_me_quote_ai_ml,quote_ai_ai=dev_me_quote_ai_ai")
    assert [m.name for m in promote.plan(MANIFEST, names)] == [
        "dbxarchitectlab_dev.dev_me_quote_ai_ml.win_model",
        "dbxarchitectlab_dev.dev_me_quote_ai_ai.extract_requirements",
        "dbxarchitectlab_dev.dev_me_quote_ai_ai.compare_explain"]


def test_cd_reads_one_value_from_the_manifest(capsys):
    assert promote.main(["--get", "models.win_model"]) == 0
    assert capsys.readouterr().out.strip() == str(promote.load_manifest()["models"]["win_model"])
