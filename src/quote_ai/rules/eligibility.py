"""Product eligibility, per stated requirement (the requirement contract, plan 4.6).

Only business truth decides: an attribute counts only if its evidence is verified. An extracted claim (a description
that says "chemo-tested") is never enough, and "unknown" never becomes a match. Suitability only: whether the
product can ship in time is fulfilment's question (rules/fulfilment.py).
"""
from __future__ import annotations

from collections.abc import Iterable

from quote_ai.domain.models import (Check, Eligibility, EligibilityResult, Evidence, Product, RequirementCheck,
                                    Requirements, TruthLayer)


def _attribute(evidence: Iterable[Evidence], attribute: str, expected: object, requirement: str) -> RequirementCheck:
    business = [e for e in evidence if e.attribute == attribute and e.layer == TruthLayer.BUSINESS]
    if not business:
        return RequirementCheck(requirement=requirement, result=Check.UNKNOWN,
                                reason=f"no verified evidence for {attribute}")
    used = [e.source_id for e in business]
    if any(e.value != expected for e in business):
        return RequirementCheck(requirement=requirement, result=Check.MISMATCH, evidence=used,
                                reason=f"{attribute} is not {expected!r}")
    return RequirementCheck(requirement=requirement, result=Check.MATCH, evidence=used)


def _sizes(product: Product, sizes: list[str]) -> RequirementCheck:
    """A sellable SKU per size, on a verified SKU <-> GUDID mapping (plan 3.3)."""
    for size in sizes:
        sku = product.sku_for(size)
        if sku is None or not sku.sellable:
            return RequirementCheck(requirement="sizes", result=Check.MISMATCH, reason=f"no sellable SKU in {size}")
        if not sku.mapping_verified:
            return RequirementCheck(requirement="sizes", result=Check.UNKNOWN,
                                    reason=f"{sku.sku_id}: mapping not verified")
    return RequirementCheck(requirement="sizes", result=Check.MATCH,
                            evidence=[product.sku_for(s).sku_id for s in sizes])


def check_product(product: Product, req: Requirements, evidence: list[Evidence],
                  evidence_snapshot: str) -> EligibilityResult:
    ev = [e for e in evidence if e.product_id == product.product_id]
    checks: list[RequirementCheck] = []
    if req.material is not None:
        checks.append(_attribute(ev, "material", req.material, "material"))
    if req.powder_free:
        checks.append(_attribute(ev, "powder_free", True, "powder_free"))
    if req.intended_use is not None:
        checks.append(_attribute(ev, "intended_use", req.intended_use, "intended_use"))
    if req.latex_free:
        checks.append(_attribute(ev, "latex_free", True, "latex_free"))
    if req.chemo_tested:
        checks.append(_attribute(ev, "chemo_tested", True, "chemo_tested"))
    # sterility: soft for Lakeside ("non-sterile is fine"), so no rule unless sterile is required
    if req.sterile:
        checks.append(_attribute(ev, "sterile", True, "sterile"))
    if req.sizes:
        checks.append(_sizes(product, req.sizes))
    checks.append(_attribute(ev, "in_commercial_distribution", True, "in_distribution"))

    results = {c.result for c in checks}
    if Check.MISMATCH in results:
        status = Eligibility.EXCLUDED
    elif Check.UNKNOWN in results:
        status = Eligibility.NEEDS_EVIDENCE
    else:
        status = Eligibility.ELIGIBLE
    return EligibilityResult(product_id=product.product_id, status=status, checks=checks,
                             evidence_snapshot=evidence_snapshot)
