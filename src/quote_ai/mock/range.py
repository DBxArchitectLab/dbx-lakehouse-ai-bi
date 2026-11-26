"""DBxLab's wider glove range (AI-15): synthetic glove families G to R that make search worth having. Demo data.

Gloves A to F (quote_ai.mock.catalog) are the Lakeside tracers and stay exactly as they were; nothing here changes
them, their price book, their stock or the quote history of Season 3. The range sits next to them so that a search
for "gloves for handling chemotherapy drugs, no latex" has vinyl, neoprene, latex, surgical, dental and discontinued
gloves to tell apart. Catalog-only: the range is in the SKU search document and the eligibility engine, not in AI-02's
price book or inventory, so a range glove can be shortlisted and compared but never priced (AI-18 refuses a SKU
with no price book row).

  products()   one row per family, as dbxlab_product
  skus()       one SKU per family and size, as dbxlab_sku (catalog numbers DBX-<code>-<size>-<per>)
  devices()    a synthetic GUDID-like record per family: description and structured fields (latex labelling,
               sterility, distribution, FDA product code)
  evidence()   the evidence rows the rules read (quote_ai.product.evidence): GUDID fields verified, AI-read values
               unverified, spec sheets and chemo reports verified by demo:qa (demo data only)
  truth()      what each family really is: the oracle the eligibility and search cases are written from (AI-17)

Written to quote_ai_product.dbxlab_range and quote_ai_product.range_evidence by src/quote_ai/jobs/search_job.py seed.
"""
from __future__ import annotations

from datetime import datetime, timezone

# letter, name, material, use, code, sizes, gloves per box, description, product code, latex (no_nrl / contains),
# sterile, in distribution, powder-free, chemo report verified, extracted extras
_RANGE = [
    ("G", "Glove G, nitrile exam, chemo-tested, small sizes", "nitrile", "exam", "NS", ["XS", "S", "M"], 100,
     "Nitrile exam glove, powder-free, textured fingertips, 9.5 in, tested for use with chemotherapy drugs, "
     "sizes XS to M", "LZC", "no_nrl", False, True, True, True),
    ("H", "Glove H, nitrile exam, chemo-tested, large sizes", "nitrile", "exam", "NX", ["L", "XL", "XXL"], 100,
     "Nitrile exam glove, powder-free, textured, 9.5 in, tested for use with chemotherapy drugs, sizes L to XXL",
     "LZC", "no_nrl", False, True, True, True),
    ("I", "Glove I, vinyl exam, powder-free", "vinyl", "exam", "VE", ["S", "M", "L", "XL"], 100,
     "Vinyl examination glove, powder-free, latex-free, smooth, 9 in, general purpose", "LYZ", "no_nrl", False,
     True, True, False),
    ("J", "Glove J, neoprene surgical, sterile, chemo-tested", "neoprene", "surgical", "CS", ["S", "M", "L", "XL"],
     50, "Neoprene surgical glove, sterile, powder-free, latex-free, beaded cuff, tested for use with chemotherapy "
     "drugs", "KGO", "no_nrl", True, True, True, True),
    ("K", "Glove K, latex surgical, sterile", "latex", "surgical", "LS", ["S", "M", "L", "XL"], 50,
     "Latex surgical glove, sterile, powder-free, micro-textured, beaded cuff", "KGO", "contains", True, True, True,
     False),
    ("L", "Glove L, thin nitrile, dental, extended cuff", "nitrile", "exam", "ND3", ["XS", "S", "M"], 100,
     "Thin nitrile exam glove for dental use, 3 mil, extended cuff 12 in, powder-free, latex-free, textured "
     "fingertips", "LZA", "no_nrl", False, True, True, False),
    ("M", "Glove M, nitrile exam, chemo-tested (discontinued)", "nitrile", "exam", "NM", ["S", "M", "L", "XL"], 100,
     "Nitrile exam glove, powder-free, textured, 9.5 in, tested for use with chemotherapy drugs, latex-free",
     "LZC", "no_nrl", False, False, True, True),
    ("N", "Glove N, polyisoprene surgical, sterile, chemo-tested", "polyisoprene", "surgical", "PS",
     ["S", "M", "L", "XL"], 50, "Polyisoprene surgical glove, sterile, powder-free, synthetic, latex-free, tested "
     "for use with chemotherapy drugs", "KGO", "no_nrl", True, True, True, True),
    ("O", "Glove O, nitrile exam, extended cuff", "nitrile", "exam", "NO", ["XS", "S", "M", "L", "XL"], 100,
     "Nitrile exam glove, powder-free, latex-free, extended cuff 12 in, textured", "LZA", "no_nrl", False, True,
     True, False),
    ("P", "Glove P, latex exam, powdered", "latex", "exam", "LE", ["S", "M", "L", "XL"], 100,
     "Latex examination glove, lightly powdered, smooth, natural rubber latex", "LYY", "contains", False, True,
     False, False),
    ("Q", "Glove Q, heavy-duty nitrile exam, 6 mil", "nitrile", "exam", "NH", ["S", "M", "L", "XL"], 100,
     "Heavy-duty nitrile exam glove, 6 mil, black, powder-free, latex-free, diamond texture", "LZA", "no_nrl",
     False, True, True, False),
    ("R", "Glove R, vinyl exam, powdered", "vinyl", "exam", "VP", ["S", "M", "L", "XL"], 100,
     "Vinyl examination glove, powdered, latex-free, economy", "LYZ", "no_nrl", False, True, False, False),
]
AT = datetime(2026, 11, 20, tzinfo=timezone.utc)
DEMO_QA = "demo:qa"


def _pid(letter: str) -> str:
    return f"GLOVE-{letter}"


def products() -> list[dict]:
    return [{"product_id": _pid(l), "name": n, "material": m, "intended_use": u, "powder_free": pf,
             "latex_free": nrl != "contains", "demo_data": True}
            for l, n, m, u, _, _, _, _, _, nrl, _, _, pf, _ in _RANGE]


def skus() -> list[dict]:
    return [{"sku_id": f"{_pid(l)}-{s}", "product_id": _pid(l), "catalog_number": f"DBX-{code}-{s}-{per}", "size": s,
             "material": m, "intended_use": u, "unit": "box", "gloves_per_unit": per, "sellable": True,
             "mapping_verified": True, "demo_data": True}
            for l, _, m, u, code, sizes, per, *_ in _RANGE for s in sizes]


def devices() -> dict[str, tuple[str, dict]]:
    """product_id -> (GUDID-like description, structured fields), like evidence_store.DEMO_DEVICES."""
    out = {}
    for l, _, _, _, _, _, _, desc, code, nrl, sterile, dist, *_ in _RANGE:
        fields = {"labeled_no_nrl" if nrl == "no_nrl" else "labeled_contains_nrl": "true",
                  "device_sterile": str(sterile).lower(), "in_commercial_distribution": str(dist).lower(),
                  "product_codes": code}
        out[_pid(l)] = (desc, fields)
    return out


def _extracted(desc: str, material: str, use: str, pf: bool) -> dict:
    """What an AI Function reads from the description (a deterministic stand-in)."""
    d = desc.lower()
    out = {"material": material, "use_type": use, "powder_free": True if "powder-free" in d else None}
    if "chemotherapy" in d:
        out["chemo_claim"] = True
    if "latex-free" in d:
        out["latex_free"] = True
    return {k: v for k, v in out.items() if v is not None}


def evidence() -> list:
    """quote_ai.product.evidence.EvidenceRow per SKU: the same builders as A to F, plus demo:qa supplier rows."""
    from quote_ai.domain.models import SourceType, Verification
    from quote_ai.product import evidence as ev
    rows = []
    for i, (l, _, m, u, _, sizes, _, desc, _, _, _, _, pf, chemo) in enumerate(_RANGE):
        pid = _pid(l)
        _, fields = devices()[pid]
        for j, s in enumerate(sizes):
            sku = f"{pid}-{s}"
            di = f"0099000{i:02d}{j:02d}"
            rows += ev.evidence_rows(di, fields, _extracted(desc, m, u, pf), AT, product_id=pid, sku_id=sku)
            supplier = [("material", m, f"SPEC-{l}"), ("powder_free", pf, f"SPEC-{l}")]
            if chemo:
                supplier.append(("chemo_tested", True, f"RPT-CHEMO-{l}"))
            for attr, value, doc in supplier:
                rows.append(ev.EvidenceRow(
                    product_id=pid, sku_id=sku, attribute_name=attr, attribute_value=ev.value_str(value),
                    source_type=SourceType.SUPPLIER_DOCUMENT, source_id=f"{doc}:v1", document_id=doc,
                    document_version=1, extraction_method=ev.DOCUMENT_METHOD,
                    test_method="ASTM D6978" if attr == "chemo_tested" else None,
                    verification_status=Verification.VERIFIED, verified_by=DEMO_QA, verified_at=AT, observed_at=AT))
    return rows


def truth() -> dict[str, dict]:
    """What each range family is, by design (the oracle for the evaluation cases): material, use, sizes, gloves per
    box, latex_free, sterile, in distribution, powder-free and whether its chemo testing is verified."""
    return {_pid(l): {"material": m, "intended_use": u, "sizes": sizes, "gloves_per_unit": per,
                      "latex_free": nrl != "contains", "sterile": sterile, "in_distribution": dist,
                      "powder_free": pf, "chemo_verified": chemo, "description": desc}
            for l, _, m, u, _, sizes, per, desc, _, nrl, sterile, dist, pf, chemo in _RANGE}
