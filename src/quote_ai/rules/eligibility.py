"""Product eligibility, per stated requirement (the requirement contract, plan 4.6; the engine of AI-15).

Only business truth decides: an attribute counts only if its evidence is verified. An extracted claim (a description
that says "chemo-tested") is never enough, and "unknown" never becomes a match. Suitability only: whether the
product can ship in time is fulfilment's question (rules/fulfilment.py).

Every check returns verified_match, verified_mismatch or unknown, with the evidence it used and a reason. One check
per hard requirement the customer stated (CHECKS); a soft requirement (price, "non-sterile is fine") gets none:

  requirement       evidence (business truth only)                      check
  material          spec sheet (supplier document, verified)            check_material
  powder_free       spec sheet                                          check_powder_free
  intended_use      FDA product code (GUDID, structured)                check_exam_use
  latex_free        GUDID natural rubber latex labelling                check_latex_free
  chemo_tested      supplier test report that passed AI-09's checks     check_chemo_tested
  sterile           GUDID sterility (only when sterile is required)     check_sterile
  sizes             a sellable SKU per size, in the confirmed unit,     check_sizes
                    on a verified SKU <-> GUDID mapping (AI-03)
  in_distribution   GUDID commercial distribution status                check_distribution

The status: all match -> eligible; any mismatch -> excluded; otherwise needs_evidence.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable

from quote_ai.domain.models import (Check, Eligibility, EligibilityResult, Evidence, Product, RequirementCheck,
                                    Requirements, TruthLayer)

MATCH, MISMATCH, UNKNOWN = Check.MATCH, Check.MISMATCH, Check.UNKNOWN


def _claims(evidence: Iterable[Evidence], attribute: str) -> list[str]:
    """What is on file for the attribute but is not business truth: shown, never used to decide."""
    return [f"{e.source_id} ({e.source_type.value}, {e.verification.value})" for e in evidence
            if e.attribute == attribute and e.layer != TruthLayer.BUSINESS]


def _attribute(evidence: Iterable[Evidence], attribute: str, expected: object, requirement: str,
               matched: str = "", missed: str = "") -> RequirementCheck:
    evidence = list(evidence)
    business = [e for e in evidence if e.attribute == attribute and e.layer == TruthLayer.BUSINESS]
    claims = _claims(evidence, attribute)
    if not business:
        return RequirementCheck(requirement=requirement, result=UNKNOWN, claims=claims,
                                reason=f"no verified evidence for {attribute}"
                                       + ("; on file: extracted claims only" if claims else ""))
    used = [e.source_id for e in business]
    if any(e.value != expected for e in business):
        got = sorted({str(e.value) for e in business})
        return RequirementCheck(requirement=requirement, result=MISMATCH, evidence=used, claims=claims,
                                reason=missed or f"{attribute} is {', '.join(got)}, not {expected!r}")
    return RequirementCheck(requirement=requirement, result=MATCH, evidence=used, claims=claims,
                            reason=matched or f"verified: {', '.join(used)}")


def check_material(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "material", req.material, "material")


def check_powder_free(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "powder_free", True, "powder_free", missed="not powder-free (verified)")


def check_exam_use(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "intended_use", req.intended_use, "intended_use")


def check_latex_free(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "latex_free", True, "latex_free", matched="GUDID: labeled no natural rubber latex",
                      missed="GUDID: labeled contains natural rubber latex")


def check_chemo_tested(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "chemo_tested", True, "chemo_tested", matched="supplier test report, verified")


def check_sterile(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "sterile", True, "sterile")


def check_distribution(ev: list[Evidence], req: Requirements) -> RequirementCheck:
    return _attribute(ev, "in_commercial_distribution", True, "in_distribution",
                      missed="GUDID: not in commercial distribution")


def check_sizes(product: Product, req: Requirements) -> RequirementCheck:
    """A sellable SKU per size, in the confirmed selling unit and glove count, on a verified mapping (plan 3.3)."""
    for size in req.sizes:
        sku = product.sku_for(size)
        if sku is None or not sku.sellable:
            return RequirementCheck(requirement="sizes", result=MISMATCH, reason=f"no sellable SKU in {size}")
        if req.unit and sku.unit != req.unit:
            return RequirementCheck(requirement="sizes", result=MISMATCH,
                                    reason=f"{sku.sku_id} is sold by the {sku.unit}, not the {req.unit}")
        if req.gloves_per_unit and sku.gloves_per_unit != req.gloves_per_unit:
            return RequirementCheck(requirement="sizes", result=MISMATCH,
                                    reason=f"{sku.sku_id} is a {sku.unit} of {sku.gloves_per_unit}, "
                                           f"not {req.gloves_per_unit}")
        if not sku.mapping_verified:
            return RequirementCheck(requirement="sizes", result=UNKNOWN,
                                    reason=f"{sku.sku_id}: mapping not verified")
    return RequirementCheck(requirement="sizes", result=MATCH,
                            evidence=[product.sku_for(s).sku_id for s in req.sizes],
                            reason="a sellable SKU in every size")


# requirement -> (is it stated?, the check). Sizes and distribution read the product, the others the evidence.
CHECKS: dict[str, tuple[Callable[[Requirements], bool], Callable]] = {
    "material": (lambda r: r.material is not None, check_material),
    "powder_free": (lambda r: bool(r.powder_free), check_powder_free),
    "intended_use": (lambda r: r.intended_use is not None, check_exam_use),
    "latex_free": (lambda r: bool(r.latex_free), check_latex_free),
    "chemo_tested": (lambda r: bool(r.chemo_tested), check_chemo_tested),
    "sterile": (lambda r: bool(r.sterile), check_sterile),     # soft for Lakeside: "non-sterile is fine"
    "sizes": (lambda r: bool(r.sizes), None),
    "in_distribution": (lambda r: True, check_distribution),   # always: never quote what isn't distributed
}


def stated(req: Requirements) -> list[str]:
    """The hard requirements this request states: each one gets exactly one check."""
    return [name for name, (is_stated, _) in CHECKS.items() if is_stated(req)]


def status_of(checks: Iterable[RequirementCheck]) -> Eligibility:
    results = {c.result for c in checks}
    if MISMATCH in results:
        return Eligibility.EXCLUDED
    if UNKNOWN in results:
        return Eligibility.NEEDS_EVIDENCE      # unknown is never a match
    return Eligibility.ELIGIBLE


def check_product(product: Product, req: Requirements, evidence: list[Evidence],
                  evidence_snapshot: str) -> EligibilityResult:
    ev = [e for e in evidence if e.product_id == product.product_id]
    checks: list[RequirementCheck] = []
    for name in stated(req):
        fn = CHECKS[name][1]
        checks.append(check_sizes(product, req) if name == "sizes" else fn(ev, req))
    return EligibilityResult(product_id=product.product_id, status=status_of(checks), checks=checks,
                             evidence_snapshot=evidence_snapshot)
