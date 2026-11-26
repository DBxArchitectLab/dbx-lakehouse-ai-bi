"""Glove attributes from a description (AI-08): the typed form the extraction fills, one schema for three callers.

  AttributeForm        the fields and their types (allowed lists as enums): what ai_query's responseFormat and the
                       chat response_format both send, and what quote_ai.product.evidence normalizes
  response_format()    the JSON schema for a chat request or ai_query(responseFormat => ...)
  extract_attributes   one description through the extraction model service (the product facts suite; the
                       pipeline runs the same form in SQL over the whole glove subset)
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel

from quote_ai.llm import endpoints, prompts
from quote_ai.llm.client import chat
from quote_ai.product.evidence import EXTRACT_FIELDS


class AttributeForm(BaseModel):
    material: Literal["nitrile", "latex", "vinyl", "neoprene", "polyisoprene", "other"] | None = None
    powder_free: bool | None = None
    textured: bool | None = None
    length_in: float | None = None
    chemo_claim: bool | None = None
    use_type: Literal["exam", "surgical"] | None = None
    latex_free: bool | None = None


assert list(AttributeForm.model_fields) == EXTRACT_FIELDS, "the form and the evidence vocabulary must agree"


def json_schema() -> dict:
    s = AttributeForm.model_json_schema()
    s["additionalProperties"] = False
    return s


def response_format() -> dict:
    return {"type": "json_schema", "json_schema": {"name": "glove_attributes", "schema": json_schema(),
                                                   "strict": True}}


def response_format_sql() -> str:
    """The same schema as a string literal for ai_query(..., responseFormat => '<this>') in the pipeline."""
    return json.dumps(response_format()).replace("'", "''")


def extract_attributes(description: str, endpoint: str | None = None, client: Any = None,
                       template: str = prompts.ATTRIBUTES_V1, cache: Any = None,
                       public_version_number: int | None = None, prompt_version: str = "v1") -> dict:
    """The form for one description, validated; anything outside the schema raises. AI-23 (E4): with a cache
    (quote_ai.llm.cache.LlmCache), an unchanged record (same GUDID publicVersionNumber, prompt version and model) is
    read once; a new record version, prompt or model is asked again."""
    endpoint = endpoint or endpoints.model(endpoints.EXTRACT)

    def call() -> dict:
        r = chat(prompts.fill(template, description=description), endpoint=endpoint, temperature=0.0, client=client,
                 response_format=response_format())
        return AttributeForm.model_validate_json(r.text).model_dump()
    if cache is None:
        return call()
    from quote_ai.llm.cache import version_of
    return cache.get_or_call("glove_attributes", {"description": description},
                             version_of(f"extract_glove_attributes/{prompt_version}", endpoint, public_version_number),
                             call)
