"""Knowledge chunks (AI-16): section-aware chunking of supplier documents, the sales playbook and GMDN notes.

A chunk keeps its section title inside its text ("Chemo permeation\nCarmustine: 45 min ..."), so a table never loses
the header that says what its numbers are. Every chunk says where it came from and how far it can be trusted:

  chunk_id      <doc_id>:<version>:<section number>       (fixed500 chunks: <doc_id>:<version>:f<n>)
  doc_id, doc_version, section, product_id ("*" = about no product in particular), sku_ids
  account_id    "public", or the customer's account for a customer-specific document (a negotiated price sheet):
                retrieval filters on it before anything reaches a prompt (AI-17)
  truth_layer   business_truth (a supplier document version that passed AI-09's checks), extracted_claim (a
                description, a marketing sheet, an unverified document), source_truth (the playbook, GMDN notes,
                price sheets: reference text, not evidence about a glove)
  chunking      section (AI-16) or fixed500 (the baseline AI-17 compares against)

Plain Python: the knowledge job (src/quote_ai/jobs/knowledge_job.py) and the local suites run the same code.
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from quote_ai.search.retriever import GENERAL, PUBLIC

LAYERS = ("business_truth", "extracted_claim", "source_truth")
# a line that opens a section: a known heading, or a short line ending with ":" or in Title Case on its own
HEADINGS = ("Specification", "Physical properties", "Chemo permeation", "Packaging", "Drugs tested",
            "Chemotherapy drug permeation test report", "Products covered", "Supplier price list", "DBxLab note")
_HEADING = re.compile(r"^(?:#+\s*)?([A-Z][A-Za-z ,/()-]{2,60}):?\s*$")


def is_front_matter(title: str) -> bool:
    """The document's header and its list of products covered: who and what, not what it says about a glove."""
    return title in ("Header", "Document", "Products covered") or title.startswith("DBxLab Medical Supply")


def sections(text: str) -> list[dict]:
    """Split a parsed document into sections: [{"title", "text"}]. A heading starts a section; the lines before
    the first heading form a "Header" section."""
    out: list[dict] = []
    title, lines = "Header", []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped and is_front_matter(title) and any(l.strip() for l in lines):
            # a blank line closes the document's header or its product list: what follows is the body
            out.append({"title": title, "text": "\n".join(l for l in lines if l.strip())})
            title, lines = "Details", []
            continue
        head = stripped.split(".")[0] if stripped.startswith(HEADINGS) else None
        m = _HEADING.match(stripped) if not head else None
        if head or (m and not stripped.endswith((".", ",")) and len(stripped.split()) <= 6 and ":" not in stripped[:-1]):
            if any(l.strip() for l in lines):
                out.append({"title": title, "text": "\n".join(l for l in lines if l.strip())})
            title = (head or m.group(1)).strip().rstrip(":")
            lines = [stripped[len(head):].lstrip(". ")] if head and stripped[len(head):].strip(". ") else []
            continue
        lines.append(line)
    if any(l.strip() for l in lines):
        out.append({"title": title, "text": "\n".join(l for l in lines if l.strip())})
    for sec in out:                       # the block of document ID, version, date and supplier
        if sec["title"] == "Details" and sec["text"].startswith("Document ID:"):
            sec["title"] = "Document"
    return out


def chunk_by_section(doc_id: str, version: int, parsed_sections: Iterable[dict], product_id: str = GENERAL,
                     sku_ids: list[str] | None = None, account_id: str | None = None,
                     truth_layer: str = "source_truth") -> list[dict]:
    """One chunk per section, the title kept inside the text."""
    if truth_layer not in LAYERS:
        raise ValueError(f"unknown truth layer {truth_layer!r}")
    return [{"chunk_id": f"{doc_id}:{version}:{i}", "doc_id": doc_id, "doc_version": int(version),
             "product_id": product_id, "sku_ids": list(sku_ids or []), "account_id": account_id or PUBLIC,
             "section": sec["title"], "truth_layer": truth_layer, "chunking": "section",
             "text": f"{sec['title']}\n{sec['text']}"}
            for i, sec in enumerate(parsed_sections)]


def chunk_fixed(doc_id: str, version: int, text: str, tokens: int = 500, product_id: str = GENERAL,
                sku_ids: list[str] | None = None, account_id: str | None = None,
                truth_layer: str = "source_truth") -> list[dict]:
    """The baseline: fixed windows of about `tokens` words, blind to sections (a table may be split in half)."""
    words = (text or "").split()
    out = []
    for n, start in enumerate(range(0, max(len(words), 1), tokens)):
        piece = " ".join(words[start:start + tokens])
        if piece:
            out.append({"chunk_id": f"{doc_id}:{version}:f{n}", "doc_id": doc_id, "doc_version": int(version),
                        "product_id": product_id, "sku_ids": list(sku_ids or []), "account_id": account_id or PUBLIC,
                        "section": "", "truth_layer": truth_layer, "chunking": "fixed500", "text": piece})
    return out


def chunk_document(doc_id: str, version: int, text: str, **meta) -> list[dict]:
    """Both chunk sets of one document: section-aware (AI-16) and fixed (AI-17's baseline)."""
    return chunk_by_section(doc_id, version, sections(text), **meta) + chunk_fixed(doc_id, version, text, **meta)
