"""What the app reads about evidence and documents (AI-08, AI-09), and where an uploaded request PDF goes.

  SqlEvidenceStore    quote_ai_product.glove_evidence (product_evidence + supplier_quality_evidence) and
                      product_documents, through the app's warehouse. Read-only: nothing is recomputed in the app.
  SqlDocumentStore    a request PDF into the documents volume (Files API), parsed with ai_parse_document on the
                      warehouse; the text then goes through the same requirements extraction as an email (AI-05)
  DemoEvidenceStore,  the same answers without a workspace (tests, a local run): synthetic devices for Gloves A to F,
  DemoDocumentStore   the evidence built by quote_ai.product.evidence, the documents by quote_ai.mock.documents
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Protocol

from quote_ai.mock import catalog as mock
from quote_ai.mock import documents as mock_docs
from quote_ai.product import evidence as ev

COLUMNS = ("di, product_id, sku_id, attribute_name, attribute_value, unit, source_type, source_id, document_id, "
           "document_version, test_method, extraction_method, verification_status, reason, verified_by, verified_at, "
           "observed_at")


class EvidenceStore(Protocol):
    def for_product(self, key: str) -> list[dict]: ...          # key: a sku_id or a product_id
    def documents(self, product_id: str) -> list[dict]: ...
    # AI-15: the rows the eligibility engine reads for several products (A to F and the wider range), and the name
    # of the evidence snapshot they come from
    def for_products(self, product_ids: list[str]) -> list[dict]: ...
    def snapshot(self) -> str: ...


class DocumentStore(Protocol):
    def upload_request(self, data: bytes, filename: str) -> str: ...
    def read_text(self, path: str) -> str: ...


class SqlEvidenceStore:
    def __init__(self, sql, names: dict[str, str]) -> None:
        self.sql, self.n = sql, names

    def for_product(self, key: str) -> list[dict]:
        return self.sql.query(f"SELECT {COLUMNS} FROM {self.n['product']}.glove_evidence "
                              f"WHERE sku_id = :k OR product_id = :k ORDER BY sku_id, attribute_name, source_type",
                              {"k": key})

    def for_products(self, product_ids: list[str]) -> list[dict]:
        """{product}.eligibility_evidence: glove_evidence and the wider range's evidence (AI-15)."""
        if not product_ids:
            return []
        marks = ", ".join(f":p{i}" for i in range(len(product_ids)))
        return self.sql.query(f"SELECT {COLUMNS} FROM {self.n['product']}.eligibility_evidence "
                              f"WHERE product_id IN ({marks})", {f"p{i}": p for i, p in enumerate(product_ids)})

    def snapshot(self) -> str:
        """The evidence tables' Delta versions, e.g. product_evidence@v41+supplier_quality_evidence@v12. Read just
        before the evidence, so a quote names what it was decided on (a write in between is possible; AI-20's
        Lakebase snapshot closes that gap). Verify: DESCRIBE HISTORY through the warehouse returns `version`."""
        parts = []
        for t in ("product_evidence", "supplier_quality_evidence"):
            rows = self.sql.query(f"DESCRIBE HISTORY {self.n['product']}.{t} LIMIT 1")
            parts.append(f"{t}@v{rows[0]['version']}" if rows else f"{t}@v?")
        return "+".join(parts)

    def documents(self, product_id: str) -> list[dict]:
        docs = self.sql.query(f"SELECT * FROM {self.n['product']}.product_documents WHERE product_id = :p "
                              f"ORDER BY document_id, version", {"p": product_id})
        rows = self.sql.query(f"SELECT {COLUMNS} FROM {self.n['product']}.glove_evidence "
                              f"WHERE product_id = :p AND document_id IS NOT NULL", {"p": product_id})
        return _with_evidence(docs, rows)


def _with_evidence(docs: list[dict], rows: list[dict]) -> list[dict]:
    """Each document with the evidence it verified, left unverified, or that a newer version superseded."""
    out = []
    for d in docs:
        mine = [r for r in rows if r["document_id"] == d["document_id"]
                and int(r["document_version"]) == int(d["version"])]
        out.append(dict(d) | {"evidence": mine,
                              "verified": sorted({r["attribute_name"] for r in mine
                                                  if r["verification_status"] == "verified"}),
                              "superseded": sorted({r["attribute_name"] for r in mine
                                                    if r["verification_status"] == "superseded"})})
    return out


_SAFE_PATH = re.compile(r"^/Volumes/[A-Za-z0-9_]+/[A-Za-z0-9_]+/[A-Za-z0-9_]+/requests/[A-Za-z0-9_.-]+\.pdf$")


class SqlDocumentStore:
    def __init__(self, sql, volume_full_name: str) -> None:
        catalog, schema, volume = volume_full_name.split(".")
        self.sql, self.root = sql, f"/Volumes/{catalog}/{schema}/{volume}"

    def upload_request(self, data: bytes, filename: str) -> str:
        """Verify before recording: WorkspaceClient().files.upload(path, stream, overwrite=False) with the app's
        service principal (WRITE VOLUME through the app's volume resource)."""
        import io

        from databricks.sdk import WorkspaceClient
        path = f"{self.root}/requests/{uuid.uuid4().hex}.pdf"
        WorkspaceClient().files.upload(path, io.BytesIO(data), overwrite=False)
        return path

    def read_text(self, path: str) -> str:
        """ai_parse_document on the warehouse. Verify: read_files on a single volume file with binaryFile."""
        if not _SAFE_PATH.match(path):
            raise ValueError(f"not a request in the documents volume: {path!r}")
        rows = self.sql.query(f"SELECT {ev.PARSED_TEXT_SQL} AS text FROM (SELECT ai_parse_document(content) AS "
                              f"parsed FROM read_files('{path}', format => 'binaryFile'))")
        return (rows[0]["text"] if rows else "") or ""


# ------------------------------------------------------------------ the local demo

# Synthetic GUDID-like records for the DBxLab products, and what an AI Function reads from each description (a
# deterministic stand-in). Every description of A to D claims chemo testing; E is labelled as containing latex;
# F's description says latex-free while its label says otherwise: the conflict the episode shows.
DEMO_DEVICES = {
    "GLOVE-A": ("Nitrile exam glove, powder-free, textured fingertips, 9.5 in, tested for use with chemotherapy drugs",
                {"labeled_no_nrl": "true", "device_sterile": "false", "in_commercial_distribution": "true",
                 "product_codes": "LZA"}),
    "GLOVE-B": ("Nitrile exam glove, powder-free, textured fingertips, 9.5 in, tested for use with chemotherapy drugs",
                {"labeled_no_nrl": "true", "device_sterile": "false", "in_commercial_distribution": "true",
                 "product_codes": "LZA"}),
    "GLOVE-C": ("Nitrile examination glove, PF, fully textured, 9.6 in, chemo tested",
                {"labeled_no_nrl": "true", "device_sterile": "false", "in_commercial_distribution": "true",
                 "product_codes": "LZA"}),
    "GLOVE-D": ("Powder free nitrile exam glove, fingertip textured, 12 in cuff, chemotherapy drug tested",
                {"labeled_no_nrl": "true", "device_sterile": "false", "in_commercial_distribution": "true",
                 "product_codes": "LZA"}),
    "GLOVE-E": ("Nitrile exam glove, powder-free, textured, 9.5 in, chemo rated",
                {"labeled_contains_nrl": "true", "device_sterile": "false", "in_commercial_distribution": "true",
                 "product_codes": "LZA"}),
    "GLOVE-F": ("Nitrile exam glove, powder-free, latex-free, 9.5 in, box of 200",
                {"labeled_contains_nrl": "true", "device_sterile": "false", "in_commercial_distribution": "true",
                 "product_codes": "LZA"}),
}
_EXTRACTED = {
    "GLOVE-A": {"material": "nitrile", "powder_free": True, "textured": True, "length_in": 9.5, "chemo_claim": True,
                "use_type": "exam"},
    "GLOVE-C": {"material": "nitrile", "powder_free": True, "textured": True, "length_in": 9.6, "chemo_claim": True,
                "use_type": "exam"},
    "GLOVE-D": {"material": "nitrile", "powder_free": True, "textured": True, "length_in": 12.0, "chemo_claim": True,
                "use_type": "exam"},
    "GLOVE-E": {"material": "nitrile", "powder_free": True, "textured": True, "length_in": 9.5, "chemo_claim": True,
                "use_type": "exam"},
    "GLOVE-F": {"material": "nitrile", "powder_free": True, "length_in": 9.5, "use_type": "exam",
                "latex_free": True},
}
_EXTRACTED["GLOVE-B"] = _EXTRACTED["GLOVE-A"]


class DemoEvidenceStore:
    """The episode's state after AI-09: Glove A's report signed off (verified), Glove B with none."""

    def __init__(self, scenarios=("glove_a_report",), signoffs=None, snapshot: str = "product_evidence@v41",
                 include_range: bool = True) -> None:
        at = datetime(2026, 11, 20, tzinfo=timezone.utc)
        rows: list[ev.EvidenceRow] = []
        for i, s in enumerate(mock.skus()):
            desc, fields = DEMO_DEVICES[s["product_id"]]
            di = f"0081234500{i:04d}"
            rows += ev.evidence_rows(di, fields, _EXTRACTED[s["product_id"]], at, product_id=s["product_id"],
                                     sku_id=s["sku_id"])
        doc_rows, self._docs, self._registry = mock_docs.demo_evidence(
            scenarios, signoffs if signoffs is not None else {("RPT-CHEMO-A", 1): "qa.demo"}, at)
        self.signoffs = signoffs if signoffs is not None else {("RPT-CHEMO-A", 1): "qa.demo"}
        if include_range:                                   # AI-15: the wider range G to R
            from quote_ai.mock import range as mock_range
            rows += mock_range.evidence()
        self.rows = [r.table_row() for r in rows + doc_rows]
        self._snapshot = snapshot

    def for_products(self, product_ids: list[str]) -> list[dict]:
        return [r for r in self.rows if r["product_id"] in set(product_ids)]

    def snapshot(self) -> str:
        return self._snapshot

    def for_product(self, key: str) -> list[dict]:
        return [r for r in self.rows if key in (r["sku_id"], r["product_id"])]

    def documents(self, product_id: str) -> list[dict]:
        signed = {k: v for k, v in self.signoffs.items()}
        docs = [{"product_id": r["product_id"], "document_id": r["document_id"], "document_type": r["document_type"],
                 "version": r["version"], "effective_date": r["effective_date"], "is_current": r["is_current"],
                 "received_via": r["received_via"],
                 "verified_by": signed.get((r["document_id"], r["version"]))
                 or ("demo:qa" if mock_docs.demo_signed(next(d for d in self._docs
                                                              if d.document_id == r["document_id"]
                                                              and d.version == r["version"])) else None)}
                for r in self._registry if r["product_id"] == product_id]
        return _with_evidence(docs, [r for r in self.rows if r["product_id"] == product_id and r["document_id"]])


class DemoDocumentStore:
    """Uploads kept in memory; "parsing" reads the text of a PDF the generator wrote (or any text-only PDF)."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def upload_request(self, data: bytes, filename: str) -> str:
        path = f"/Volumes/demo/quote_ai_ai/quote_ai_documents/requests/{uuid.uuid4().hex}.pdf"
        self.files[path] = data
        return path

    def read_text(self, path: str) -> str:
        text = mock_docs.pdf_text(self.files[path])
        # the request body only: drop the generator's header lines
        return "\n".join(l for l in text.splitlines()
                         if l and not l.startswith(("DBxLab Medical Supply", mock_docs.DEMO, "Document ID:",
                                                    "Version:", "Effective date:", "Supplier:")))
