"""Unity Gateway (AI-06): the model service names, their definitions as code, and the Model access suite's logic."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from quote_ai.llm import gateway_checks as gc
from quote_ai.llm.client import Reply

ROOT = Path(__file__).resolve().parents[2]
SERVICES = sorted((ROOT / "resources" / "quote_ai").glob("quote_ai_*.model_service.yml"))


def test_endpoint_names_include_target(monkeypatch):
    monkeypatch.setenv("BUNDLE_TARGET", "prod")
    import quote_ai.llm.endpoints as e
    importlib.reload(e)
    assert e.CHAT == "quote_ai_chat_prod"
    monkeypatch.setenv("BUNDLE_TARGET", "dev-shared")
    importlib.reload(e)
    assert (e.EXTRACT, e.EMBED) == ("quote_ai_extract_dev_shared", "quote_ai_embed_dev_shared")
    monkeypatch.delenv("BUNDLE_TARGET")
    importlib.reload(e)
    assert e.CHAT == "quote_ai_chat_dev"


def test_extract_goes_through_the_extract_service(monkeypatch):
    import quote_ai.llm.endpoints as e
    from quote_ai.llm import prompts
    from quote_ai.llm.extract import extract_requirements
    monkeypatch.setenv("BUNDLE_TARGET", "dev")
    monkeypatch.delenv("QUOTE_AI_AI_SCHEMA", raising=False)
    importlib.reload(e)
    sent = {}

    def create(**kw):
        sent.update(kw)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"quantity": 1}'))],
                               usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    extract_requirements("1 box", client=client, template=prompts.EXTRACT)
    assert sent["model"] == "quote_ai_extract_dev"


def test_three_services_one_per_use_case():
    keys = {k for f in SERVICES for k in yaml.safe_load(f.read_text())["resources"]["model_services"]}
    # AI-23 adds quote_ai_chat_small: E3's candidate for the comparison, still one service per use once chosen
    assert keys == {"quote_ai_extract", "quote_ai_chat", "quote_ai_embed", "quote_ai_chat_small"}


@pytest.mark.parametrize("path", SERVICES, ids=lambda p: p.name)
def test_each_service_is_governed_as_code(path):
    (key, svc), = yaml.safe_load(path.read_text())["resources"]["model_services"].items()
    assert svc["model_service_id"] == f"{key}_${{var.quote_ai_target}}"        # the name carries the target
    assert svc["parent"].endswith("${resources.schemas.quote_ai_ai.name}")
    cfg = svc["config"]
    dest = cfg["routing"]["destinations"][0]
    assert dest["pay_per_token_config"]["model"].startswith("models/${var.")      # the model is a variable
    assert cfg["rate_limits"] and all("renewal_period" in r for r in cfg["rate_limits"])
    assert cfg["inference_table"]["parent"].endswith("${resources.schemas.quote_ai_monitoring.name}")
    assert all(g["privileges"] == ["EXECUTE"] for g in svc["grants"])


def test_chat_is_for_reps_with_a_per_user_limit_of_20_a_minute():
    svc = yaml.safe_load((ROOT / "resources/quote_ai/quote_ai_chat.model_service.yml").read_text())
    chat = svc["resources"]["model_services"]["quote_ai_chat"]
    assert [g["principal"] for g in chat["grants"]] == ["${var.quote_ai_reps_group}"]
    assert chat["config"]["rate_limits"] == [{"key": "RATE_LIMIT_KEY_USER_DEFAULT", "requests": 20,
                                              "renewal_period": "RATE_LIMIT_RENEWAL_PERIOD_MINUTE"}]


def test_every_variable_the_services_use_is_declared():
    declared = set(yaml.safe_load((ROOT / "databricks.yml").read_text())["variables"])
    import re
    used = {m for f in SERVICES for m in re.findall(r"\$\{var\.(\w+)\}", f.read_text())}
    assert used <= declared, used - declared


# ------------------------------------------------------------------ Model access suite logic

class HttpError(Exception):
    def __init__(self, status_code, msg=""):
        super().__init__(msg)
        self.status_code = status_code


def test_outcomes_are_named_from_what_came_back():
    assert gc.outcome(Reply("A nitrile glove is ...", None, 0.2)) == "allowed"
    assert gc.outcome(HttpError(403, "PERMISSION_DENIED")) == "no_grant"
    assert gc.outcome(HttpError(429, "REQUEST_LIMIT_EXCEEDED")) == "rate_limited"
    assert gc.outcome(Reply("This request was blocked by a service policy (PII).", None, 0.1)) == "blocked"
    assert gc.outcome(HttpError(500, "boom")) == "error"


def test_the_suite_one_allowed_three_refused():
    cases = {c["id"]: c for c in gc.load_cases()}
    assert [c["expected"] for c in cases.values()] == ["allowed", "no_grant", "rate_limited", "blocked",
                                                       "blocked", "blocked"]          # AI-24: sec-06, sec-07
    limit = cases["over-the-limit"]
    assert gc.verdict(limit, ["allowed"] * 20 + ["rate_limited"] * 5)
    assert not gc.verdict(limit, ["allowed"] * 25)                      # the limit never applied
    assert not gc.verdict(cases["no-grant"], ["allowed"])
