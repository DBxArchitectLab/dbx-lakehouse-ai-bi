"""SKU <-> GUDID package level: an automatic match proposes, a person decides (AI-03, plan 3.3).

  auto_match   a SKU against one package level of its device: matched on catalog number and package level, with a
               confidence (high / medium / low). Every match starts unverified.
  verify       a person confirms the unit; only now can the SKU be offered in a quote.
  reconcile    the next run: a GUDID package that changed marks a verified mapping superseded (back to review).
  review_queue unverified and superseded mappings, waiting for a person.

Never infer an orderable SKU from a package quantity alone: a GUDID package of 100 is not proof that DBxLab sells that
box, and a case is never "a box".
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping as MappingT
from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from quote_ai.domain.models import Sku
from quote_ai.product.device_mapping import UnresolvedPackage, gloves_per_unit, unit_level


class Status(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    REJECTED = "rejected"


class Confidence(str, Enum):
    HIGH = "high"        # catalog number and unit agree, and the glove count is the SKU's
    MEDIUM = "medium"    # catalog number agrees, the unit or the count does not (a case for a box SKU)
    LOW = "low"          # no DBxLab SKU, or no catalog number match


class DeviceMapping(BaseModel):
    """One row of quote_ai_product.product_device_mapping."""
    mapping_id: str
    sku_id: str | None
    product_id: str | None
    primary_di: str | None
    package_di: str
    unit_level: str | None              # each / box / case, from the package hierarchy
    gloves_per_unit: int | None         # None: the package levels did not resolve
    mapping_method: str = "auto:catalog_number+package_level"
    mapping_confidence: Confidence
    verification_status: Status = Status.UNVERIFIED
    package_fingerprint: str | None = None   # what GUDID said about the package when it was matched
    reason: str = ""
    verified_by: str | None = None
    verified_at: datetime | None = None


def fingerprint(levels: Iterable[MappingT]) -> str:
    """A hash of the device's package levels: a change in GUDID (a new quantity, a new level) changes it."""
    rows = sorted((str(lv.get("di")), str(lv.get("contains_di")), str(lv.get("pkg_quantity")))
                  for lv in levels)
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def _norm(s: str | None) -> str:
    return "".join(ch for ch in (s or "").upper() if ch.isalnum())


def auto_match(sku: MappingT | None, package_di: str, catalog_number: str | None = None,
               levels: list[dict] | None = None, primary_di: str | None = None) -> DeviceMapping:
    """Match a DBxLab SKU (a dict like a dbxlab_sku row, or None) to one GUDID package level. Always unverified."""
    levels = levels or []
    gloves, unit, why = None, None, []
    try:
        gloves = gloves_per_unit(levels, package_di, base_di=primary_di) if levels else None
        unit = unit_level(levels, package_di, base_di=primary_di) if levels else None
    except UnresolvedPackage as e:
        why.append(str(e))
    if levels and gloves is None and not why:
        why.append("package levels did not resolve")
    sku_id = sku.get("sku_id") if sku else None
    if sku is None:
        confidence = Confidence.LOW
        why.append("no DBxLab SKU for this package")
    elif not catalog_number or _norm(catalog_number) != _norm(sku.get("supplier_catalog_number")):
        confidence = Confidence.LOW
        why.append("catalog number does not match")
    elif gloves is not None and gloves == sku.get("gloves_per_unit") and unit == sku.get("unit"):
        confidence = Confidence.HIGH
    else:
        confidence = Confidence.MEDIUM
        why.append(f"package is a {unit or '?'} of {gloves or '?'}, the SKU is a {sku.get('unit')} of "
                   f"{sku.get('gloves_per_unit')}")
    return DeviceMapping(mapping_id=f"{sku_id or '-'}|{package_di}", sku_id=sku_id,
                         product_id=sku.get("product_id") if sku else None, primary_di=primary_di,
                         package_di=package_di, unit_level=unit, gloves_per_unit=gloves,
                         mapping_confidence=confidence, package_fingerprint=fingerprint(levels) if levels else None,
                         reason="; ".join(why))


def match_device(sku: MappingT, catalog_number: str | None, levels: list[dict], primary_di: str) -> list[DeviceMapping]:
    """Every package level of the device bound to a SKU (each, box, case), each a candidate with its confidence.
    Only one should ever be verified: the level whose unit and count are the SKU's."""
    dis = [primary_di] + [str(lv["di"]) for lv in levels if str(lv["di"]) != primary_di
                          and (lv.get("contains_di") or lv.get("pkg_quantity"))]
    return [auto_match(sku, di, catalog_number, levels, primary_di) for di in dict.fromkeys(dis)]


def verify(m: DeviceMapping, reviewer: str, at: datetime) -> DeviceMapping:
    """A person confirms the unit. Refused when nothing could be sold: no SKU, or no resolved glove count."""
    if m.sku_id is None:
        raise ValueError(f"{m.package_di}: no DBxLab SKU; a package with no SKU is never offered")
    if m.gloves_per_unit is None:
        raise ValueError(f"{m.mapping_id}: package levels unresolved; fix the levels before verifying")
    return m.model_copy(update={"verification_status": Status.VERIFIED, "verified_by": reviewer, "verified_at": at,
                                "mapping_method": m.mapping_method.replace("auto:", "reviewed:")})


def reject(m: DeviceMapping, reviewer: str, at: datetime, reason: str) -> DeviceMapping:
    return m.model_copy(update={"verification_status": Status.REJECTED, "verified_by": reviewer, "verified_at": at,
                                "reason": reason})


def offerable(m: DeviceMapping) -> bool:
    """Only a verified mapping, to a DBxLab SKU, with a resolved glove count, can be offered in a quote."""
    return m.verification_status == Status.VERIFIED and m.sku_id is not None and m.gloves_per_unit is not None


def reconcile(new: Iterable[DeviceMapping], existing: Iterable[DeviceMapping]) -> list[DeviceMapping]:
    """The next matching run against what reviewers decided. Same package: the decision stands. Changed package:
    a verified (or rejected) mapping becomes superseded and goes back to review, with the new counts."""
    before = {m.mapping_id: m for m in existing}
    out = []
    for m in new:
        old = before.pop(m.mapping_id, None)
        if old is None or old.verification_status == Status.UNVERIFIED:
            out.append(m)
        elif old.package_fingerprint == m.package_fingerprint:
            out.append(old)
        else:
            out.append(m.model_copy(update={"verification_status": Status.SUPERSEDED,
                                            "reason": "GUDID package changed since it was verified"}))
    # a level that disappeared from GUDID: a decision on it no longer stands
    out += [o.model_copy(update={"verification_status": Status.SUPERSEDED, "reason": "no longer in GUDID"})
            if o.verification_status == Status.VERIFIED else o for o in before.values()]
    return out


def review_queue(mappings: Iterable[DeviceMapping]) -> list[DeviceMapping]:
    """Unverified and superseded mappings, the most confident first: what a person checks next."""
    order = {Confidence.HIGH: 0, Confidence.MEDIUM: 1, Confidence.LOW: 2}
    waiting = [m for m in mappings if m.verification_status in (Status.UNVERIFIED, Status.SUPERSEDED)]
    return sorted(waiting, key=lambda m: (order[m.mapping_confidence], m.mapping_id))


def levels_by_device(package_rows: Iterable[MappingT]) -> dict[str, list[dict]]:
    """glove_packages rows (primary_di, package_di, contains_di, pkg_quantity) -> the package levels per device."""
    out: dict[str, list[dict]] = {}
    for r in package_rows:
        out.setdefault(str(r["primary_di"]), []).append(
            {"di": str(r["package_di"]), "contains_di": r.get("contains_di"), "pkg_quantity": r.get("pkg_quantity")})
    return out


def box_quantity(levels: list[dict], primary_di: str) -> int | None:
    """The quantity of the device's first package level (a box of N base devices), if there is exactly one."""
    boxes = {int(lv["pkg_quantity"]) for lv in levels
             if lv.get("pkg_quantity") and str(lv.get("contains_di")) == primary_di}
    return boxes.pop() if len(boxes) == 1 else None


def match_catalog(sku_rows: Iterable[MappingT], device_rows: Iterable[MappingT], package_rows: Iterable[MappingT],
                  existing: Iterable[DeviceMapping] = ()) -> list[DeviceMapping]:
    """One matching run: every SKU bound to a GUDID device, against every package level of that device, reconciled
    with what reviewers decided before. device_rows: glove_devices (primary_di, catalog_number)."""
    devices = {str(d["primary_di"]): d for d in device_rows}
    levels = levels_by_device(package_rows)
    new: list[DeviceMapping] = []
    for sku in sku_rows:
        di = sku.get("supplier_di")
        if not di or di not in devices:
            continue
        new += match_device(sku, devices[di].get("catalog_number"), levels.get(di, []), di)
    return reconcile(new, existing)


def to_domain_sku(sku: MappingT, mappings: Iterable[DeviceMapping]) -> Sku:
    """The domain SKU the rules read: mapping_verified only when a verified mapping gives its exact glove count."""
    ok = any(offerable(m) and m.sku_id == sku["sku_id"] and m.gloves_per_unit == sku["gloves_per_unit"]
             for m in mappings)
    return Sku(sku_id=sku["sku_id"], product_id=sku["product_id"], size=sku["size"], unit=sku["unit"],
               gloves_per_unit=sku["gloves_per_unit"], sellable=sku["sellable"], mapping_verified=ok)
