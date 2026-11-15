"""Smoke tests (AI-01): the package imports, the first client is measured, and every schema is declared once."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_the_package_imports():
    for m in ("quote_ai", "quote_ai.rules", "quote_ai.services", "quote_ai.llm.client", "quote_ai.ingest.engine"):
        importlib.import_module(m)


def test_chat_returns_the_answer_tokens_and_seconds():
    from quote_ai.llm.client import chat

    sent = {}

    def create(**kw):
        sent.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="I can't check your stock."))],
                               usage=SimpleNamespace(prompt_tokens=50, completion_tokens=8, total_tokens=58))

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    r = chat("Lakeside ...", endpoint="ep", system="You are a sales assistant.", client=client)
    assert r.text == "I can't check your stock." and r.usage.total_tokens == 58 and r.seconds >= 0
    assert sent["temperature"] == 0.0 and [m["role"] for m in sent["messages"]] == ["system", "user"]


def test_every_schema_and_volume_is_declared_once_with_a_comment():
    keys: dict[str, list[str]] = {"schemas": [], "volumes": []}
    for f in sorted((ROOT / "resources").rglob("*.yml")):
        res = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("resources", {})
        for kind in keys:
            for key, spec in (res.get(kind) or {}).items():
                keys[kind].append(key)
                assert spec.get("comment"), f"{f.name}: {kind}.{key} has no comment"
    for kind, names in keys.items():
        assert len(names) == len(set(names)), f"{kind} declared twice: {names}"
    assert {"quote_ai_product", "quote_ai_ai", "quote_ai_gudid_bronze", "quote_ai_ops"} <= set(keys["schemas"])
    ours = [k for k in keys["schemas"] if k != "lakehouse"]
    assert all(k.startswith("quote_ai_") for k in ours), ours


def test_every_quote_ai_object_is_prefixed_and_lives_in_resources_quote_ai():
    """Isolation: QuoteAI deploys only from resources/quote_ai/, and every object it declares is named quote_ai_*."""
    for f in sorted((ROOT / "resources" / "quote_ai").glob("*.yml")):
        assert f.name.startswith("quote_ai"), f.name
        res = (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).get("resources", {})
        for kind, objs in res.items():
            for key, spec in objs.items():
                assert key.startswith("quote_ai_"), f"{f.name}: {kind}.{key}"
                name = spec.get("name", "")
                if kind in ("schemas", "volumes", "jobs", "pipelines"):
                    assert name.startswith("quote_ai_"), f"{f.name}: {kind}.{key} is named {name!r}"
    for f in (ROOT / "resources").glob("*.yml"):
        assert "quote_ai" not in f.read_text(encoding="utf-8"), \
            f"{f.name}: QuoteAI resources belong in resources/quote_ai/"
