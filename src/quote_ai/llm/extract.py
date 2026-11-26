"""extract_requirements (AI-05): a quote request in, validated requirements out.

The prompt is loaded from the Prompt Registry by its alias (production until AI-23, champion since), sent at temperature 0 with a response_format
built from the Requirements model, and the answer is validated by that same model: anything that doesn't match the
schema raises instead of reaching the rules. Traced with MLflow, so every output can be tied to the prompt version
that produced it.

The prompt URI comes from the caller or the environment (QUOTE_AI_PROMPT_URI, e.g.
prompts:/dbxarchitectlab_dev.quote_ai_ai.extract_requirements@champion); there is no silent local fallback.
"""
from __future__ import annotations

import os
from typing import Any

from quote_ai.llm import endpoints, prompts
from quote_ai.llm.client import chat
from quote_ai.llm.requirements_schema import Requirements, response_format


def _trace(fn):
    """mlflow.trace when MLflow is installed (Databricks, the app); unchanged in unit tests without it."""
    try:
        import mlflow
    except ImportError:
        return fn
    return mlflow.trace(fn)


def prompt_uri(catalog: str, schema: str = "quote_ai_ai", alias: str = prompts.CHAMPION) -> str:
    return f"prompts:/{prompts.full_name(catalog, schema)}@{alias}"


def load_template(uri: str) -> tuple[str, str]:
    """The template and its version from the registry. Verify: mlflow.genai.load_prompt and PromptVersion.template."""
    import mlflow
    p = mlflow.genai.load_prompt(uri)
    return p.template, str(p.version)


class ExtractionError(ValueError):
    """The model's answer did not match the requirements schema."""


@_trace
def extract_requirements(request: str, endpoint: str | None = None, client: Any = None, uri: str | None = None,
                         template: str | None = None) -> Requirements:
    """Read one request. `template` is for tests and the notebook (a version not yet registered)."""
    endpoint = endpoint or endpoints.model(endpoints.EXTRACT)   # AI-06: through Unity Gateway
    version = "local"
    if template is None:
        uri = uri or os.environ.get("QUOTE_AI_PROMPT_URI")
        if not uri:
            raise ValueError("no prompt: pass uri= or set QUOTE_AI_PROMPT_URI (the champion alias)")
        template, version = load_template(uri)
    r = chat(prompts.render(template, request), endpoint=endpoint, temperature=0.0, client=client,
             response_format=response_format())
    _tag({"prompt_uri": uri or "local", "prompt_version": version, "endpoint": endpoint,
          "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens, "latency_s": r.latency_s})
    try:
        return Requirements.model_validate_json(r.text)
    except Exception as e:  # pydantic.ValidationError or a non-JSON answer
        raise ExtractionError(f"the answer does not match the requirements schema: {e}") from e


def _tag(attrs: dict) -> None:
    """Prompt version, tokens and latency on the current trace span, when tracing is on."""
    try:
        import mlflow
        span = mlflow.get_current_active_span()
        if span is not None:
            span.set_attributes(attrs)
    except Exception:  # noqa: BLE001  tracing must never break an extraction
        pass
