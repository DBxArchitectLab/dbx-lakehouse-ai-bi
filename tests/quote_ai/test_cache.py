"""Optimization without losing control (AI-23): a safe cache, prompts by alias with no fallback, models by config,
and the retriever variant of experiment E2."""
import json
from types import SimpleNamespace

import pytest

from quote_ai.llm import cache as C
from quote_ai.llm import prompts
from quote_ai.llm.client import cost_usd, model_for, models_config


# ------------------------------------------------------------------ the cache

def test_cache_key_changes_when_the_gudid_public_version_number_changes():
    payload = {"description": "Nitrile exam glove, powder-free, chemo tested"}
    v1 = C.version_of("extract_glove_attributes/v1", "quote_ai_extract_dev", 3)
    v2 = C.version_of("extract_glove_attributes/v1", "quote_ai_extract_dev", 4)
    assert C.cache_key("glove_attributes", payload, v1) == C.cache_key("glove_attributes", dict(payload), v1)
    assert C.cache_key("glove_attributes", payload, v1) != C.cache_key("glove_attributes", payload, v2)


@pytest.mark.parametrize("kind, payload", [
    ("price_quote", {"product_id": "GLOVE-A"}),
    ("stock", {"sku_id": "GLOVE-C-M"}),
    ("glove_attributes", {"description": "x", "unit_price": 8.6}),
    ("knowledge", {"question": "what does Lakeside pay", "filters": {"account_id": ["ACC-001"]}}),
    ("explain", {"question": "q", "rep": "rep.alice"}),
])
def test_price_stock_and_account_dependent_calls_are_never_cached(kind, payload):
    assert not C.cacheable(kind, payload)
    with pytest.raises(C.NotCacheable):
        C.cache_key(kind, payload, "v")
    cache, calls = C.LlmCache(), []
    for _ in range(2):
        cache.get_or_call(kind, payload, "v", lambda: calls.append(1) or {"ok": True})
    assert len(calls) == 2 and cache.bypassed == 2 and not cache.store.rows


def test_the_cache_is_bypassed_when_the_prompt_or_model_version_changes():
    cache, calls = C.LlmCache(), []
    payload = {"description": "Glove A"}

    def ask(prompt_v, model):
        return cache.get_or_call("glove_attributes", payload, C.version_of(prompt_v, model, 7),
                                 lambda: calls.append((prompt_v, model)) or {"material": "nitrile"})
    ask("v1", "quote_ai_extract_dev")
    ask("v1", "quote_ai_extract_dev")                 # unchanged: a hit
    ask("v2", "quote_ai_extract_dev")                 # new prompt: asked again
    ask("v1", "quote_ai_chat_small_dev")              # new model: asked again
    assert calls == [("v1", "quote_ai_extract_dev"), ("v2", "quote_ai_extract_dev"),
                     ("v1", "quote_ai_chat_small_dev")]
    assert (cache.hits, cache.misses) == (1, 3)


def test_extract_attributes_asks_once_per_record_version():
    sent = []

    def create(**kw):
        sent.append(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {"material": "nitrile", "powder_free": True, "textured": None, "length_in": None, "chemo_claim": True,
             "use_type": "exam", "latex_free": True})))], usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    from quote_ai.llm.attributes import extract_attributes
    cache = C.LlmCache()
    for pvn in (1, 1, 1, 2):
        extract_attributes("Glove A nitrile exam", "ep", client, cache=cache, public_version_number=pvn)
    assert len(sent) == 2 and cache.hits == 2


def test_the_sql_store_keys_rows_by_cache_key():
    sent = []
    sql = SimpleNamespace(execute=lambda s, p=None: sent.append((s, p)), query=lambda s, p=None: [])
    store = C.SqlCacheStore(sql, "cat.quote_ai_ai")
    assert store.get("k") is None
    store.put("k", "glove_attributes", "v", {"a": 1})
    assert sent[0][0].startswith("MERGE INTO cat.quote_ai_ai.llm_cache") and sent[0][1]["value"] == '{"a": 1}'
    assert "CREATE TABLE IF NOT EXISTS {ai}.llm_cache" in C.DDL.read_text(encoding="utf-8")


# ------------------------------------------------------------------ prompts by alias

def test_load_prompt_falls_back_to_an_error_not_a_default_prompt_when_the_alias_is_missing():
    def registry(uri):
        if uri.endswith("@champion"):
            return SimpleNamespace(template="T {{question}}", version=4)
        raise RuntimeError("RESOURCE_DOES_NOT_EXIST: alias not found")
    assert prompts.load_prompt("compare_explain", "champion", "cat", "quote_ai_ai", loader=registry) == \
        ("T {{question}}", "compare_explain/v4")
    with pytest.raises(prompts.PromptAliasMissing, match="challenger"):
        prompts.load_prompt("compare_explain", prompts.CHALLENGER, "cat", "quote_ai_ai", loader=registry)
    with pytest.raises(prompts.PromptAliasMissing):
        prompts.load_prompt("compare_explain", "champion", None)


def test_compare_explain_v4_is_shorter_with_the_table_first_and_the_same_rules():
    v1, v4 = prompts.COMPARE_EXPLAIN_V1, prompts.COMPARE_EXPLAIN_V4
    assert len(v4) < 0.8 * len(v1) and v4.index("{{table}}") < v4.index("{{question}}")
    for must in ("no evidence on file", "business_truth", "extracted_claim", "table:<product_id>:<attribute>"):
        assert must in v4
    assert prompts.PROMPTS["compare_explain"]["v4"][0] == v4


# ------------------------------------------------------------------ models by config

def test_every_use_has_a_service_and_a_price():
    cfg = models_config()
    assert set(cfg["uses"]) >= {"extraction", "comparison", "embedding"}
    for use in cfg["uses"]:
        assert model_for(use, cfg).service in cfg["prices"]
    m = model_for("comparison", cfg)
    assert (m.service, m.max_tokens) == ("quote_ai_chat", 600) and m.endpoint.startswith("quote_ai_chat_")
    assert model_for("comparison", cfg, {"comparison": "quote_ai_chat_small"}).service == "quote_ai_chat_small"
    with pytest.raises(KeyError):
        model_for("translation", cfg)
    assert cost_usd("quote_ai_chat", 1000, 1000, cfg) == pytest.approx(0.002)


def test_every_configured_service_is_created_by_the_bundle():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parents[2]
    declared = set()
    for f in (root / "resources" / "quote_ai").glob("*.model_service.yml"):
        declared |= set((yaml.safe_load(f.read_text(encoding="utf-8"))["resources"]["model_services"]))
    assert set(models_config()["prices"]) <= declared


# ------------------------------------------------------------------ the retriever variant (E2)

def test_the_hybrid_weight_reaches_the_index_only_when_set():
    from quote_ai.search import retriever
    seen = []
    index = SimpleNamespace(similarity_search=lambda **kw: seen.append(kw) or {})
    retriever.search_candidates(index, "nitrile", ["M"])
    retriever.search_candidates(index, "nitrile", ["M"], rerank=True, hybrid_weight=0.1)
    assert "hybrid_weight" not in seen[0] and seen[1]["hybrid_weight"] == 0.1 and seen[1]["reranker"]
    cfg = retriever.VARIANTS["hybrid_weighted_rerank"]
    assert (cfg.query_type, cfg.rerank, cfg.hybrid_weight) == ("HYBRID", True, 0.1)
