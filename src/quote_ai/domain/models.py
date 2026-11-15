"""The QuoteAI domain: requirements, evidence, products, quotes and their events.

Plain data, no I/O. Every other module (rules, services, the app, the agent's tools, the tests) speaks these types.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class QuoteState(str, Enum):
    DRAFT = "DRAFT"
    REQUIREMENTS_CONFIRMED = "REQUIREMENTS_CONFIRMED"
    SHORTLISTED = "SHORTLISTED"
    PRICED = "PRICED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    ISSUED = "ISSUED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class SourceType(str, Enum):
    GUDID = "gudid"
    SUPPLIER_DOCUMENT = "supplier_document"
    DBXLAB_MASTER_DATA = "dbxlab_master_data"
    AI_EXTRACTION = "ai_extraction"


class Verification(str, Enum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    CONFLICTING = "conflicting"
    SUPERSEDED = "superseded"


class TruthLayer(str, Enum):
    """The four kinds of truth (plan 3.6). Only business truth decides eligibility, fulfilment and price."""
    SOURCE = "source_truth"
    EXTRACTED = "extracted_claim"
    BUSINESS = "business_truth"
    GENERATED = "generated_answer"


class Check(str, Enum):
    MATCH = "verified_match"
    MISMATCH = "verified_mismatch"
    UNKNOWN = "unknown"


class Eligibility(str, Enum):
    ELIGIBLE = "eligible"
    EXCLUDED = "excluded"            # at least one verified mismatch
    NEEDS_EVIDENCE = "needs_evidence"  # no mismatch, but at least one unknown


class FulfilmentStatus(str, Enum):
    FULL = "full"
    PARTIAL = "partial"
    BACKORDER = "backorder"


# ---------------------------------------------------------------- requirements


class Requirements(BaseModel):
    """What the customer asked for. r1 is as extracted (with what is missing); r2 is clarified and confirmed by the
    rep; r3 exists only if the customer accepts an alternative, such as a later delivery date."""
    version: int = 1
    material: str | None = None
    powder_free: bool | None = None
    intended_use: str | None = None          # "exam" or "surgical"
    latex_free: bool | None = None
    chemo_tested: bool | None = None
    sterile: bool | None = None              # soft for Lakeside: non-sterile is fine
    sizes: list[str] = Field(default_factory=list)
    quantity: int | None = None
    unit: str | None = None                  # the selling unit asked for, e.g. "box"
    quantity_by_size: dict[str, int] | None = None
    gloves_per_unit: int | None = None
    target_unit_price: float | None = None   # soft
    delivery_days: int | None = None         # hard: deliver within this many days
    missing: list[str] = Field(default_factory=list)  # details the request leaves open; a person is asked

    @property
    def complete(self) -> bool:
        return not self.missing and self.quantity_by_size is not None and self.gloves_per_unit is not None


# ---------------------------------------------------------------- evidence and products


class Evidence(BaseModel):
    """One attribute claim about a product, with where it came from and how far it can be trusted (plan 3.4)."""
    product_id: str
    attribute: str
    value: object
    source_type: SourceType
    source_id: str
    verification: Verification
    document_version: int | None = None
    verified_by: str | None = None

    @property
    def layer(self) -> TruthLayer:
        if self.verification == Verification.VERIFIED:
            return TruthLayer.BUSINESS
        if self.source_type == SourceType.AI_EXTRACTION or self.source_type == SourceType.SUPPLIER_DOCUMENT:
            return TruthLayer.EXTRACTED
        return TruthLayer.SOURCE


class Sku(BaseModel):
    sku_id: str
    product_id: str
    size: str
    unit: str                    # the orderable unit, e.g. "box"
    gloves_per_unit: int
    sellable: bool
    mapping_verified: bool       # SKU <-> GUDID DI and package level, verified by a person (plan 3.3)


class Product(BaseModel):
    product_id: str
    name: str
    skus: list[Sku]

    def sku_for(self, size: str) -> Sku | None:
        return next((s for s in self.skus if s.size == size), None)


# ---------------------------------------------------------------- checks


class RequirementCheck(BaseModel):
    requirement: str
    result: Check
    evidence: list[str] = Field(default_factory=list)  # source_ids of the business-truth rows used
    reason: str = ""


class EligibilityResult(BaseModel):
    product_id: str
    status: Eligibility
    checks: list[RequirementCheck]
    evidence_snapshot: str


class LineFulfilment(BaseModel):
    size: str
    sku_id: str
    quantity: int
    status: FulfilmentStatus
    now: int                      # units that ship from stock
    later: int = 0
    later_day: int | None = None  # day the rest arrives
    ready_day: int                # day the whole line is complete
    late: bool                    # misses the requested delivery date


class FulfilmentResult(BaseModel):
    product_id: str
    lines: list[LineFulfilment]
    inventory_snapshot: str

    @property
    def on_time(self) -> bool:
        return not any(line.late for line in self.lines)


# ---------------------------------------------------------------- quotes


class QuoteLine(BaseModel):
    line_no: int
    size: str
    quantity: int
    unit: str
    sku_id: str | None = None
    unit_price: float | None = None
    list_price: float | None = None
    price_book_id: str | None = None

    @property
    def total(self) -> float | None:
        return None if self.unit_price is None else round(self.unit_price * self.quantity, 2)


class Approval(BaseModel):
    approver: str
    terms_hash: str
    quote_version: int
    at: datetime


class Quote(BaseModel):
    quote_id: str
    account: str
    rep: str
    state: QuoteState = QuoteState.DRAFT
    version: int = 1              # the terms version: requirements, lines, price; an approval binds one version
    revision: int = 1             # the row revision, for optimistic locking; every write increments it
    request_text: str
    requirements: Requirements
    lines: list[QuoteLine] = Field(default_factory=list)
    product_id: str | None = None
    evidence_snapshot: str | None = None
    inventory_snapshot: str | None = None
    discount: float | None = None
    approval_needed: str | None = None   # NONE / MANAGER
    approval: Approval | None = None


class QuoteEvent(BaseModel):
    quote_id: str
    quote_version: int
    revision: int
    action: str
    from_state: QuoteState | None
    to_state: QuoteState
    actor: str
    at: datetime
    detail: dict = Field(default_factory=dict)
