"""The requirements schema (AI-05): what the extraction model must return for a quote request.

One definition, used twice: Requirements.model_json_schema() is the response_format sent to the model, and
Requirements.model_validate_json() checks what comes back. A requirement is a value (true, false or null) and a hard
flag: "must", "need", "required" and "-free" make it hard; "non-sterile is fine" is no requirement at all (null).
What a quote needs but the request never says goes in `missing`: asked, never guessed.

to_domain() turns the answer into the domain's r1 (quote_ai.domain.models.Requirements), which the rules check.
"""
from __future__ import annotations

import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from quote_ai.domain.models import Requirements as DomainRequirements

Size = Literal["XS", "S", "M", "L", "XL", "XXL"]
Missing = Literal["quantity_per_size", "gloves_per_box", "unit", "delivery_date", "quantity", "sizes"]


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: bool | None = None
    hard: bool = False


class TargetPrice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: float
    currency: str = "USD"
    per_unit: Literal["glove", "box", "case"] | None = None


class Requirements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    material: Literal["nitrile", "latex", "vinyl", "polyisoprene"] | None = None
    use: Literal["exam", "surgical"] | None = None
    powder_free: Requirement = Requirement()
    latex_free: Requirement = Requirement()
    sterile: Requirement = Requirement()
    chemo_tested: Requirement = Requirement()
    sizes: list[Size] = Field(default_factory=list)
    quantity: float
    unit: Literal["glove", "box", "case"] | None = None
    target_price: TargetPrice | None = None
    delivery_days: int | None = None
    preferences: list[str] = Field(default_factory=list)
    notes: str | None = None
    missing: list[Missing] = Field(default_factory=list)


def _inline(schema: dict) -> dict:
    """The JSON schema with $defs inlined: flat, as structured outputs expect (verify the endpoint's limits)."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(schema)


def json_schema() -> dict:
    return _inline(Requirements.model_json_schema())


def response_format() -> dict:
    """The response_format for a chat request: the model must answer with JSON that matches the schema."""
    return {"type": "json_schema", "json_schema": {"name": "requirements", "schema": json_schema()}}


_MISSING_TO_DOMAIN = {"gloves_per_box": "gloves_per_unit"}


def _hard(r: Requirement) -> bool | None:
    """Only a hard requirement becomes a rule; a soft one is a preference, and null is no requirement."""
    return r.value if r.hard else None


def to_domain(req: Requirements) -> DomainRequirements:
    """The extraction as requirements r1: what the rules check, plus what the rep is asked (missing)."""
    per = req.target_price.per_unit if req.target_price else None
    return DomainRequirements(
        version=1, material=req.material, intended_use=req.use, powder_free=_hard(req.powder_free),
        latex_free=_hard(req.latex_free), chemo_tested=_hard(req.chemo_tested), sterile=_hard(req.sterile),
        sizes=list(req.sizes), quantity=int(req.quantity) if req.quantity is not None else None, unit=req.unit,
        target_unit_price=req.target_price.amount if req.target_price and (per is None or per == req.unit) else None,
        delivery_days=req.delivery_days, missing=[_MISSING_TO_DOMAIN.get(m, m) for m in req.missing])
