"""The SKU search document (AI-15): one row per sellable SKU, what AI Search embeds and matches.

In the workspace the document is src/quote_ai/pipelines/search_doc.sql (quote_ai_ai.sku_search_doc, change data feed
on, the source of the Delta Sync index). This module is the same document in Python, for tests, the local demo and
the evaluation suites: the same columns, and the same search text (SEARCH_TEXT_PARTS joined by SEPARATOR, in that
order; tests/quote_ai/test_search.py checks the SQL file says the same).

The search text may hold unverified claims ("tested for use with chemotherapy drugs" from a description): it only
helps find candidates. The eligibility engine never reads it (AI-17 tests that a variant which lets it decide fails).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

COLUMNS = ["sku_id", "product_id", "catalog_number", "size", "material_family", "in_commercial_distribution",
           "search_text"]
SEARCH_TEXT_PARTS = ("product_name", "brand_name", "device_description", "attribute_summary")
SEPARATOR = " | "
DEMO_BRAND = "DBxLab demo"


# how a value reads in the search text: words a rep would type
_SAY = {("latex_free", "true"): "latex-free", ("latex_free", "false"): "contains latex",
        ("powder_free", "true"): "powder-free", ("powder_free", "false"): "powdered",
        ("chemo_tested", "true"): "chemo-tested", ("sterile", "true"): "sterile", ("sterile", "false"): "non-sterile",
        ("in_commercial_distribution", "false"): "discontinued"}
_WORDS = ("material", "intended_use")


def attribute_summary(rows: Iterable[Mapping]) -> str:
    """The evidence of one SKU as a few words a rep would type: "chemo-tested, exam, latex-free, nitrile,
    powder-free". Every status counts here (a claim helps find a glove; it never makes one eligible); superseded
    rows are left out, and numeric specs are not search words."""
    out = set()
    for r in rows:
        if r["verification_status"] == "superseded":
            continue
        a, v = r["attribute_name"], str(r["attribute_value"]).lower()
        if a in _WORDS:
            out.add(v)
        elif (a, v) in _SAY:
            out.add(_SAY[(a, v)])
    return ", ".join(sorted(out))


def search_text(parts: Mapping) -> str:
    return SEPARATOR.join(str(parts.get(k) or "") for k in SEARCH_TEXT_PARTS)


def doc_row(sku: Mapping, product_name: str, material: str, description: str, distribution: bool,
            evidence_rows: Iterable[Mapping], brand: str = DEMO_BRAND) -> dict:
    return {"sku_id": sku["sku_id"], "product_id": sku["product_id"], "catalog_number": sku["catalog_number"],
            "size": sku["size"], "material_family": material, "in_commercial_distribution": distribution,
            "search_text": search_text({"product_name": product_name, "brand_name": brand,
                                        "device_description": description,
                                        "attribute_summary": attribute_summary(evidence_rows)})}


def demo_docs(evidence_rows: list[dict] | None = None) -> list[dict]:
    """The search document without a workspace: Gloves A to F and the wider range G to R (synthetic), with the
    evidence the local demo holds (quote_ai.storage.evidence_store.DemoEvidenceStore)."""
    from quote_ai.mock import catalog, range as mock_range
    from quote_ai.storage.evidence_store import DEMO_DEVICES, DemoEvidenceStore
    rows = evidence_rows if evidence_rows is not None else DemoEvidenceStore().rows
    by_sku: dict[str, list[dict]] = {}
    for r in rows:
        by_sku.setdefault(r["sku_id"], []).append(r)
    products = {p["product_id"]: p for p in catalog.products() + mock_range.products()}
    devices = DEMO_DEVICES | mock_range.devices()
    out = []
    for s in catalog.skus() + mock_range.skus():
        p = products[s["product_id"]]
        desc, fields = devices[s["product_id"]]
        out.append(doc_row(s, p["name"], p["material"], desc, fields.get("in_commercial_distribution") == "true",
                           by_sku.get(s["sku_id"], [])))
    return out
