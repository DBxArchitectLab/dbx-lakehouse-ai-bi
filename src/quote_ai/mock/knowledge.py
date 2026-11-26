"""What the knowledge index knows (AI-16, AI-17), as synthetic demo data for DBxLab Medical Supply.

  PLAYBOOK, GMDN_NOTES   DBxLab's own sales playbook and short notes on glove categories (written for the demo; no
                         standards text, no real GMDN definitions)
  PRICE_SHEETS           negotiated price sheets, one per customer account: customer-confidential, so each chunk
                         carries its account_id and only that account's reps may retrieve it
  REP_ACCOUNTS           which accounts each rep owns (quote_ai_sales.rep_account in the workspace). The draft's
                         rep_south (Lakeside) is rep.alice; rep_north is rep.bob
  documents()            every knowledge source as (doc_id, version, text, meta) for the chunker, with the truth layer
                         of supplier documents taken from the evidence (verified -> business truth)
  demo_chunks()          the chunks, both chunkings: the local knowledge index of the app's demo and the suites

Accounts are AI-02's synthetic customers: ACC-001 Lakeside Regional Clinic, ACC-002 Harbor Point Dental Group,
ACC-003 Summit Oncology Center, ACC-004 Riverbend Veterinary Hospital.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

PLAYBOOK = """DBxLab sales playbook (demo data)

Exam or surgical gloves
Exam gloves are for patient examination and general care. Surgical gloves are sterile and sized for procedures. A ward
that asks for surgical gloves needs sterile surgical gloves; an exam glove is not a substitute.

Chemotherapy handling
A chemo claim counts only with a test report that names the drugs, the test standard and a result for every drug,
checked by DBxLab QA. A description or a marketing sheet that says chemo-tested is a claim, not evidence. Never offer
a glove for chemo use on a claim alone.

Latex-free wards
Latex-free comes from the GUDID natural rubber latex labelling. A glove labelled as containing natural rubber latex
is never offered to a latex-free ward.

Talking about quality
Compare verified specifications side by side: AQL, thickness and tensile strength with their units and the document
version. Never give a quality score; an FDA device class is not a quality ranking. Say which values are unverified.

Delivery dates
A glove that misses the customer's hard delivery date is an alternative. Offer it only with the date it can ship and
ask the customer to accept the later date first.

Discounts
Up to 5 percent off list a rep decides. More needs a sales manager's approval on that quote version.
"""

GMDN_NOTES = """Glove categories: DBxLab notes (demo data)

Examination glove
A non-sterile or sterile glove worn on the hands of healthcare staff during patient examination, to protect patient
and wearer from contamination. FDA product codes LZA (polymer), LYY (latex), LYZ (vinyl), LZC (specialty).

Surgical glove
A sterile glove worn by the surgical team during procedures. FDA product code KGO.

Chemotherapy glove
A glove tested for resistance to permeation by chemotherapy drugs, with a breakthrough time per drug in its test report.
"""

PRICE_SHEETS = {
    "ACC-001": """Negotiated price sheet: Lakeside Regional Clinic (customer-confidential, demo data)

Contract prices
Lakeside Regional Clinic pays 8.20 USD per box for Glove A from 300 boxes, and 8.35 USD per box for Glove D.

Terms
Lakeside Regional Clinic contract valid until 2027-03-31. Delivery within 10 days of the order is the standard term.
""",
    "ACC-002": """Negotiated price sheet: Harbor Point Dental Group (customer-confidential, demo data)

Contract prices
Harbor Point Dental Group pays 6.90 USD per box for Glove L, thin nitrile, dental, from 200 boxes.

Terms
Harbor Point Dental Group contract valid until 2027-06-30.
""",
}
REP_ACCOUNTS = {"rep.alice": ["ACC-001", "ACC-004"], "rep.bob": ["ACC-002", "ACC-003"]}
ALIASES = {"rep_south": "rep.alice", "rep_north": "rep.bob"}


def accounts_of(user: str) -> list[str]:
    """A signed-in user's accounts: "rep.alice@dbxlab.example" -> rep.alice -> [ACC-001, ACC-004]."""
    rep = user.split("@", 1)[0]
    return list(REP_ACCOUNTS.get(ALIASES.get(rep, rep), []))


def _verified_docs(evidence_rows: Iterable[Mapping]) -> set[tuple[str, int]]:
    return {(r["document_id"], int(r["document_version"])) for r in evidence_rows
            if r.get("document_id") and r["verification_status"] == "verified"}


def sources(supplier_docs: Iterable[tuple], descriptions: Mapping[str, str], skus: Mapping[str, list[str]],
            verified: set[tuple[str, int]]) -> list[tuple]:
    """Every knowledge source as (doc_id, version, text, meta), for the chunker. supplier_docs: (doc_id, version,
    parsed text, product_id) of the current versions; verified: the (doc_id, version) with verified evidence ->
    business truth, else extracted claim. descriptions: product_id -> GUDID description (an extracted claim).
    The knowledge job and the local demo both build the chunks through here."""
    out = []
    for doc_id, version, text, pid in supplier_docs:
        layer = "business_truth" if (doc_id, int(version)) in verified else "extracted_claim"
        out.append((doc_id, int(version), text, {"product_id": pid, "sku_ids": skus.get(pid), "truth_layer": layer}))
    for pid, desc in descriptions.items():
        out.append((f"GUDID-DESC-{pid.split('-')[-1]}", 1, f"GUDID device description\n{desc}",
                    {"product_id": pid, "sku_ids": skus.get(pid), "truth_layer": "extracted_claim"}))
    out.append(("PLAYBOOK", 1, PLAYBOOK, {"truth_layer": "source_truth"}))
    out.append(("GMDN-NOTES", 1, GMDN_NOTES, {"truth_layer": "source_truth"}))
    for account, text in PRICE_SHEETS.items():
        out.append((f"PS-{account}", 1, text, {"account_id": account, "truth_layer": "source_truth"}))
    return out


def documents(evidence_rows: list[dict], scenarios: Iterable[str] = ("glove_a_report",)) -> list[tuple]:
    """The local demo's sources: the generated supplier PDFs as ai_parse_document would read them (current versions
    of spec sheets, test reports and marketing sheets), the synthetic GUDID descriptions, playbook and price sheets."""
    from quote_ai.mock import catalog, documents as mock_docs, range as mock_range
    from quote_ai.storage.evidence_store import DEMO_DEVICES
    docs = mock_docs.documents(None, scenarios)
    current = {(r["document_id"], int(r["version"])) for r in mock_docs.demo_registry(docs) if r["is_current"]}
    skus: dict[str, list[str]] = {}
    for s in catalog.skus() + mock_range.skus():
        skus.setdefault(s["product_id"], []).append(s["sku_id"])
    supplier = [(d.document_id, d.version, mock_docs.pdf_text(mock_docs.pdf_bytes(d.lines())), d.product_id)
                for d in docs if d.document_type in ("spec_sheet", "test_report", "marketing")
                and (d.document_id, d.version) in current]
    descriptions = {pid: desc for pid, (desc, _) in (DEMO_DEVICES | mock_range.devices()).items()}
    return sources(supplier, descriptions, skus, _verified_docs(evidence_rows))


def demo_chunks(evidence_rows: list[dict] | None = None) -> list[dict]:
    """Every chunk of every knowledge source, in both chunkings (the local knowledge index)."""
    from quote_ai.search.chunks import chunk_document
    from quote_ai.storage.evidence_store import DemoEvidenceStore
    rows = evidence_rows if evidence_rows is not None else DemoEvidenceStore().rows
    out = []
    for doc_id, version, text, meta in documents(rows):
        out += chunk_document(doc_id, version, text, **meta)
    return out
