"""From a GUDID package level to a selling unit (AI-03): how many gloves one orderable unit holds.

GUDID identifies a device at each package level: the base device (one glove, "each"), a box that contains N base
devices, a case that contains M boxes. identifiers.txt gives every level as a row (device_id, contains_di_number,
pkg_quantity, pkg_type). gloves_per_unit walks from the unit's DI down to the base, multiplying the quantities.

A box is data, never an assumption: a missing or broken level raises; nothing ever defaults to 100.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping


class UnresolvedPackage(ValueError):
    """A package level is missing, has no quantity, or loops: the unit can't be converted to gloves."""


def _levels(levels: Iterable[Mapping]) -> dict[str, Mapping]:
    """Package levels by DI; a row with neither contains_di nor pkg_quantity is the base device, not a package."""
    return {str(lv["di"]): lv for lv in levels if lv.get("contains_di") or lv.get("pkg_quantity")}


def base_di_of(levels: Iterable[Mapping]) -> str | None:
    """The base device: named by a row without a package (if given), else the one DI that is contained but is not
    itself a package level. None when that is ambiguous."""
    levels = list(levels)
    explicit = [str(lv["di"]) for lv in levels if not lv.get("contains_di") and not lv.get("pkg_quantity")]
    if len(explicit) == 1:
        return explicit[0]
    packages = _levels(levels)
    leaves = {str(lv["contains_di"]) for lv in packages.values() if lv.get("contains_di")} - set(packages)
    return leaves.pop() if len(leaves) == 1 else None


def gloves_per_unit(levels: list[dict], unit_di: str, base_di: str | None = None) -> int:
    """Multiply package quantities from the unit DI down to the base DI.

    levels: [{di, contains_di, pkg_quantity}], one per package level (the base device may be listed with neither).
    Raises UnresolvedPackage if a level is missing, has no positive quantity, or the chain loops: never assume 100.
    """
    by_di = _levels(levels)
    base = base_di or base_di_of(levels)
    if base is None:
        raise UnresolvedPackage(f"{unit_di}: no base device among the package levels")
    n, di, seen = 1, str(unit_di), set()
    while di != base:
        if di in seen:
            raise UnresolvedPackage(f"{unit_di}: package levels loop at {di}")
        seen.add(di)
        level = by_di.get(di)
        if level is None:
            raise UnresolvedPackage(f"unresolved package level for {unit_di}: {di} is not a known level")
        qty, contains = level.get("pkg_quantity"), level.get("contains_di")
        try:
            qty = int(qty)
        except (TypeError, ValueError):
            raise UnresolvedPackage(f"{di}: no package quantity") from None
        if qty <= 0 or not contains:
            raise UnresolvedPackage(f"{di}: package quantity {qty} of {contains!r}")
        n *= qty
        di = str(contains)
    return n


def depth(levels: list[dict], unit_di: str, base_di: str | None = None) -> int:
    """0 for the base device, 1 for a package of base devices (a box), 2 for a package of packages (a case)."""
    by_di, base = _levels(levels), base_di or base_di_of(levels)
    d, di = 0, str(unit_di)
    while di != base and di in by_di and d < 10:
        di, d = str(by_di[di]["contains_di"]), d + 1
    return d


UNIT_BY_DEPTH = {0: "each", 1: "box", 2: "case"}


def unit_level(levels: list[dict], unit_di: str, base_di: str | None = None) -> str:
    """each, box or case, from where the DI sits in the package hierarchy (not from pkg_type text)."""
    return UNIT_BY_DEPTH.get(depth(levels, unit_di, base_di), "pallet")


def units_to_gloves(quantity: int, gloves_in_unit: int) -> int:
    """400 boxes of 100 = 40,000 gloves."""
    return quantity * gloves_in_unit
