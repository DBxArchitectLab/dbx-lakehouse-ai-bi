"""The comparison table (AI-16): every value comes from data, never from the model.

Spec values from the evidence (product_evidence and supplier documents, through glove_evidence), prices from the
price book, stock from the inventory snapshot, and each glove's status from the AI-15 rules (the quote's shortlist):
on time, "alternative, needs acceptance", or needs evidence (listed, not compared as an option). No quality score:
an FDA class is not a quality ranking, and a score hides which values are verified.

  EVIDENCE_SQL       the query the app runs (SqlEvidenceStore.for_products reads the same rows)
  comparison_table   per product: verified specs, documentation completeness, fit to the request, commercial terms,
                     status from the rules, unresolved evidence
  table_markdown     the table as the RAG prompt sees it, one cell per value with its status and source
  cell_layer         the truth layer a table citation (table:<product>:<attribute>) stands for
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

EVIDENCE_SQL = """SELECT e.sku_id, e.product_id, e.attribute_name, e.attribute_value, e.unit, e.verification_status,
       e.source_type, e.source_id, e.document_id, e.document_version
FROM {product}.eligibility_evidence e
WHERE e.product_id IN (:product_ids) AND e.verification_status IN ('verified', 'unverified', 'conflicting')"""

# what a rep compares, in this order; "required" = what complete documentation means for a quote
ATTRIBUTES = ("material", "powder_free", "intended_use", "latex_free", "chemo_tested", "sterile", "aql", "thickness",
              "tensile_strength", "length")
REQUIRED = ("material", "powder_free", "intended_use", "latex_free", "chemo_tested", "aql", "thickness",
            "tensile_strength")
_RANK = {"verified": 0, "conflicting": 1, "unverified": 2}


def _cell(rows: list[Mapping]) -> dict:
    """The value shown for one attribute: the verified value if there is one; else the conflict; else the claim.
    Exactly as the evidence row says, with its status and source."""
    if not rows:
        return {"value": None, "unit": None, "status": "missing", "source_id": None, "document_id": None,
                "document_version": None, "source_type": None}
    best = sorted(rows, key=lambda r: (_RANK.get(r["verification_status"], 9), str(r["source_id"])))[0]
    return {"value": best["attribute_value"], "unit": best.get("unit"), "status": best["verification_status"],
            "source_id": best["source_id"], "document_id": best.get("document_id"),
            "document_version": best.get("document_version"), "source_type": best.get("source_type")}


def comparison_table(product_ids: list[str], names: Mapping[str, str], evidence_rows: Iterable[Mapping],
                     candidates: list[dict], price_rows: list | None = None, stock: Mapping | None = None,
                     target_unit_price: float | None = None) -> dict:
    """candidates: the quote's shortlist (QuoteService.shortlist_candidates). price_rows: PriceBookRow; stock:
    sku_id -> Stock. The eligible gloves are the columns; gloves that need evidence are listed under them."""
    rows = [r for r in evidence_rows if r["verification_status"] in _RANK]
    status = {c["product_id"]: c for c in candidates}
    waiting = [c["product_id"] for c in candidates if c["status"] == "needs_evidence"]
    columns = []
    for pid in list(product_ids) + [w for w in waiting if w not in product_ids]:
        mine = [r for r in rows if r["product_id"] == pid]
        specs = {a: _cell([r for r in mine if r["attribute_name"] == a]) for a in ATTRIBUTES}
        c = status.get(pid, {})
        prices = [p for p in (price_rows or []) if p.sku_id.startswith(pid + "-") and p.tier == "standard"
                  and p.min_units == 1]
        skus = [s for k, s in (stock or {}).items() if k.startswith(pid + "-")]
        list_price = min((p.list_price for p in prices), default=None)
        columns.append({
            "product_id": pid, "name": names.get(pid, pid), "specs": specs,
            "completeness": {"verified": sum(specs[a]["status"] == "verified" for a in REQUIRED),
                             "required": len(REQUIRED)},
            "fit": {"hard": "every hard requirement verified" if c.get("status") == "eligible" else c.get("status"),
                    "price_vs_target": None if list_price is None or target_unit_price is None
                    else round(list_price - target_unit_price, 2)},
            "commercial": {"list_price": list_price,
                           "free_stock": sum(max(s.available - s.reserved, 0) for s in skus) if skus else None,
                           "lead_time_days": max((s.lead_time_days for s in skus), default=None)},
            "status": c.get("status"), "offer": c.get("label") or ("needs evidence"
                                                                  if c.get("status") == "needs_evidence" else None),
            "unresolved": [a for a in REQUIRED if specs[a]["status"] != "verified"],
        })
    compared = [c for c in columns if c["product_id"] in product_ids]
    return {"attributes": list(ATTRIBUTES), "columns": compared,
            # listed under the table, not compared as an option until its evidence arrives; its specs are kept for
            # Ask about this product
            "needs_evidence": [c | {"failed": status[c["product_id"]].get("failed", [])}
                               for c in columns if c["product_id"] in waiting and c["product_id"] not in product_ids],
            "label": "Demo data. Values from the evidence tables and the price book; statuses from the rules."}


def table_markdown(table: dict) -> str:
    """The table for the prompt: one row per attribute, "value unit (status, source)" per glove."""
    cols = table["columns"]
    out = ["| attribute | " + " | ".join(f"{c['name']} [{c['product_id']}]" for c in cols) + " |",
           "|---|" + "---|" * len(cols)]
    for a in table["attributes"]:
        cells = []
        for c in cols:
            s = c["specs"][a]
            cells.append("no evidence on file" if s["status"] == "missing" else
                         f"{s['value']}{' ' + s['unit'] if s['unit'] else ''} ({s['status']}, {s['source_id']}; "
                         f"cite table:{c['product_id']}:{a})")
        out.append(f"| {a} | " + " | ".join(cells) + " |")
    out.append("| offer | " + " | ".join(str(c["offer"]) for c in cols) + " |")
    for n in table.get("needs_evidence", []):
        out.append(f"Needs evidence, not an option yet: {n['name']} [{n['product_id']}] ({', '.join(n['failed'])})")
    return "\n".join(out)


def cell_layer(table: dict, product_id: str, attribute: str, extra_columns: list[dict] | None = None) -> str | None:
    """table:<product>:<attribute> -> business_truth (verified), extracted_claim (unverified or conflicting), or
    no_evidence (missing); None if the table has no such cell."""
    for c in table["columns"] + table.get("needs_evidence", []) + (extra_columns or []):
        if c["product_id"] == product_id and attribute in c["specs"]:
            st = c["specs"][attribute]["status"]
            return {"verified": "business_truth", "missing": "no_evidence"}.get(st, "extracted_claim")
    return None
