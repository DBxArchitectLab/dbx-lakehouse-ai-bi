"""The model client: one chat call, measured (AI-01, extended in AI-04).

Every call returns the answer, the token usage the endpoint reports (resp.usage: prompt and completion tokens), and
the elapsed seconds timed around the call: the numbers behind cost and latency. The endpoint is always a parameter,
never hard-coded, and the temperature defaults to 0. From AI-06 the endpoint is a Unity Gateway model service
(quote_ai.llm.endpoints), with the same call.

Needs the openai package (WorkspaceClient.serving_endpoints.get_open_ai_client builds an OpenAI-compatible client).
"""
from __future__ import annotations

import time
from typing import Any, NamedTuple


class Reply(NamedTuple):
    text: str
    usage: Any          # prompt_tokens, completion_tokens, total_tokens, as reported by the endpoint
    seconds: float

    @property
    def prompt_tokens(self) -> int:
        return int(getattr(self.usage, "prompt_tokens", 0) or 0)

    @property
    def completion_tokens(self) -> int:
        return int(getattr(self.usage, "completion_tokens", 0) or 0)

    @property
    def latency_s(self) -> float:
        return self.seconds


Result = Reply   # the name AI-04 uses on screen


def workspace_client() -> Any:
    """An OpenAI-compatible client of this workspace (verify: get_open_ai_client in the installed databricks-sdk)."""
    from databricks.sdk import WorkspaceClient
    return WorkspaceClient().serving_endpoints.get_open_ai_client()


def chat(prompt: str, endpoint: str, system: str | None = None, temperature: float = 0.0,
         client: Any = None, **kwargs: Any) -> Reply:
    """One chat completion, measured. `client` is for tests; by default an OpenAI-compatible client of this
    workspace. Extra keyword arguments (response_format, max_tokens) go to the request as they are."""
    if client is None:
        client = workspace_client()
    messages = [{"role": "user", "content": prompt}]
    if system:
        messages.insert(0, {"role": "system", "content": system})
    start = time.perf_counter()
    resp = client.chat.completions.create(model=endpoint, messages=messages, temperature=temperature, **kwargs)
    secs = time.perf_counter() - start
    return Reply(resp.choices[0].message.content, resp.usage, secs)


# ------------------------------------------------------------------ AI-23: the model per use, from config

_HERE = __import__("pathlib").Path(__file__).resolve()
# The repo's config, or the copy scripts/quote_ai/build_app.py puts next to a vendored package (an app's _vendor)
MODELS_YML = next((p for p in (_HERE.parents[3] / "config" / "quote_ai" / "models.yml",
                               _HERE.parents[2] / "quote_ai_config" / "models.yml") if p.exists()),
                  _HERE.parents[3] / "config" / "quote_ai" / "models.yml")


class ModelUse(NamedTuple):
    use: str
    service: str            # the model service without the target (quote_ai_chat)
    endpoint: str           # what a chat request names (quote_ai_chat_dev, through quote_ai.llm.endpoints.model)
    max_tokens: int | None


def models_config(path=None) -> dict:
    import yaml
    from pathlib import Path
    return yaml.safe_load(Path(path or MODELS_YML).read_text(encoding="utf-8"))


def model_for(use: str, config: dict | None = None, overrides: dict | None = None) -> ModelUse:
    """The model service and token limit for one use (extraction, comparison, agent, embedding), from
    config/quote_ai/models.yml. `overrides` ({use: service}) is how an experiment changes exactly one model.
    An unknown use is an error: no default model."""
    from quote_ai.llm import endpoints
    cfg = config or models_config()
    if use not in cfg["uses"]:
        raise KeyError(f"no model configured for {use!r} (config/quote_ai/models.yml)")
    u = cfg["uses"][use]
    service = (overrides or {}).get(use, u["service"])
    return ModelUse(use, service, endpoints.model(f"{service}_{endpoints.TARGET}"), u.get("max_tokens"))


def cost_usd(service: str, prompt_tokens: int, completion_tokens: int, config: dict | None = None) -> float:
    """What a call cost at the configured prices (USD per 1,000 tokens): the experiments' cost per quote."""
    p = (config or models_config())["prices"][service]
    return prompt_tokens / 1000 * p["input"] + completion_tokens / 1000 * p["output"]
