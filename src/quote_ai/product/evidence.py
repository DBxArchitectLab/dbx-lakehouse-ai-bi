"""Evidence rows: every attribute value with its source, method and verification status (AI-08, AI-09).

AI-extracted is not verified. Precedence of sources (AI-08):
  1. structured GUDID fields (natural rubber latex labelling, sterility, distribution)  -> verified
  2. supplier documents (AI-09)                     -> verified only when all five checks pass and are recorded
  3. AI extraction from a description               -> unverified, always
Two sources that disagree are both kept and both marked conflicting, for review: nothing is silently overwritten.

  AI-08  normalize, gudid_rows, ai_rows, mark_conflicts, evidence_rows, review_rows, invariants, to_domain
  AI-09  current_versions, apply_versions, parse_quantity, verify_document, verify_chemo, spec_rows, chemo_rows,
         document_evidence

Plain Python on dicts and pydantic rows, so the Lakeflow pipelines (src/quote_ai/pipelines/attributes.py,
documents.py) run the same code that tests/quote_ai/test_evidence.py and test_document_evidence.py check.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import datetime

from pydantic import BaseModel, model_validator

from quote_ai.domain.models import Evidence, SourceType, Verification

# ------------------------------------------------------------------ the attribute vocabulary (AI-08)

MATERIALS = ("nitrile", "latex", "vinyl", "neoprene", "polyisoprene", "other", "unknown")
USE_TYPES = ("exam", "surgical", "other", "unknown")
# what the extraction returns -> (the evidence attribute the rules read, its unit)
ATTRIBUTES = {
    "material": ("material", None),
    "powder_free": ("powder_free", None),
    "textured": ("textured", None),
    "length_in": ("length", "in"),
    "chemo_claim": ("chemo_tested", None),
    "use_type": ("intended_use", None),
    "latex_free": ("latex_free", None),
}
EXTRACT_FIELDS = list(ATTRIBUTES)
LENGTH_IN = (5.0, 25.0)            # a glove length outside this is a misread, not a glove

AI_METHOD = "ai_function"
STRUCTURED = "structured_field"
DOCUMENT_METHOD = "ai_parse_document+ai_extract"
# ai_parse_document's elements flattened to text: one place for the pipeline and the app's PDF upload.
# Verify before recording: the VARIANT path of the elements and their content in the current output version.
PARSED_TEXT_SQL = ("concat_ws('\\n',transform(try_cast(parsed:document:elements AS ARRAY<VARIANT>), "
                   "e -> try_cast(e:content AS STRING)))")

_TRUE = {"true", "yes", "y", "1", "t"}
_FALSE = {"false", "no", "n", "0", "f"}


def _bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower() if v is not None else ""
    return True if s in _TRUE else False if s in _FALSE else None


def normalize(field: str, value):
    """One extracted value, typed and on the allowed list. None means "write no evidence": not stated, unknown, or not
    a value at all. A material or use outside the list becomes "other"; an implausible length is dropped."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "unknown", "null", "none", "not stated", "n/a"):
        return None
    if field == "material":
        v = str(value).strip().lower()
        return v if v in MATERIALS else "other"
    if field == "use_type":
        v = str(value).strip().lower()
        v = {"examination": "exam", "surgeon": "surgical"}.get(v, v)
        return v if v in USE_TYPES else "other"
    if field == "length_in":
        try:
            n = float(str(value).replace('"', "").replace("in", "").strip())
        except ValueError:
            return None
        return round(n, 2) if LENGTH_IN[0] <= n <= LENGTH_IN[1] else None
    if field in ("powder_free", "textured", "chemo_claim", "latex_free"):
        return _bool(value)
    return None   # a field we did not ask for


def value_str(v) -> str:
    """How a value is stored in attribute_value (STRING): true/false, numbers as written, text lower-case."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def parse_value(s: str | None):
    """attribute_value back to a Python value (what the rules compare)."""
    if s is None:
        return None
    b = _bool(s) if s.lower() in ("true", "false") else None
    if b is not None:
        return b
    try:
        return float(s)
    except ValueError:
        return s


# ------------------------------------------------------------------ the row


class EvidenceRow(BaseModel):
    """One row of quote_ai_product.product_evidence (AI-08) or supplier_quality_evidence (AI-09)."""
    di: str | None = None
    product_id: str | None = None
    sku_id: str | None = None
    attribute_name: str
    attribute_value: str
    unit: str | None = None
    source_type: SourceType
    source_id: str
    document_id: str | None = None
    document_version: int | None = None
    test_method: str | None = None
    extraction_method: str
    verification_status: Verification
    reason: str = ""
    verified_by: str | None = None
    verified_at: datetime | None = None
    observed_at: datetime

    @model_validator(mode="after")
    def _ai_is_never_verified(self):
        if self.source_type == SourceType.AI_EXTRACTION and self.verification_status == Verification.VERIFIED:
            raise ValueError(f"{self.source_id}: an AI-extracted value is never verified")
        return self

    def table_row(self) -> dict:
        d = self.model_dump()
        d["source_type"], d["verification_status"] = self.source_type.value, self.verification_status.value
        return d


# ------------------------------------------------------------------ AI-08: GUDID fields and AI values


def gudid_rows(di: str, fields: Mapping, observed_at: datetime, product_id: str | None = None,
               sku_id: str | None = None) -> list[EvidenceRow]:
    """Structured GUDID fields are source truth, verified: latex labelling, sterility, commercial distribution.
    Verify before recording: the silver column names (labeled_contains_nrl, labeled_no_nrl, device_sterile,
    in_commercial_distribution) in the current release files."""
    rows: list[EvidenceRow] = []

    def add(attr: str, value, field: str):
        rows.append(EvidenceRow(di=di, product_id=product_id, sku_id=sku_id, attribute_name=attr,
                                attribute_value=value_str(value), source_type=SourceType.GUDID,
                                source_id=f"gudid:{di}:{field}", extraction_method=STRUCTURED,
                                verification_status=Verification.VERIFIED, observed_at=observed_at))
    contains, no_nrl = _bool(fields.get("labeled_contains_nrl")), _bool(fields.get("labeled_no_nrl"))
    if contains:                                   # labelled as containing latex: never latex-free
        add("latex_free", False, "labeled_contains_nrl")
    elif no_nrl:
        add("latex_free", True, "labeled_no_nrl")
    sterile = _bool(fields.get("device_sterile"))
    if sterile is not None:
        add("sterile", sterile, "device_sterile")
    dist = _bool(fields.get("in_commercial_distribution"))
    if dist is not None:
        add("in_commercial_distribution", dist, "device_comm_distribution_status")
    use = intended_use(fields.get("product_codes"))          # AI-15: exam vs surgical from the FDA product code
    if use is not None:
        add("intended_use", use, "product_code")
    return rows


# AI-15: the FDA product code says what a glove is for (plan 4.6: "Exam gloves: FDA product code; GMDN"). Exam:
# patient examination gloves (polymer LZA, latex LYY, vinyl LYZ, specialty LZC). Surgical: surgeon's gloves (KGO).
EXAM_CODES = ("LZA", "LYY", "LYZ", "LZC")
SURGICAL_CODES = ("KGO",)


def intended_use(product_codes) -> str | None:
    """exam or surgical from a device's FDA product codes (a list, or "LZA,LZC"); None when no code says, or when
    the codes disagree (then there is no verified use, and the check stays unknown)."""
    text = " ".join(map(str, product_codes)) if isinstance(product_codes, (list, tuple)) else str(product_codes or "")
    codes = set(re.findall(r"(?<![A-Z])[A-Z]{3}(?![A-Z])", text.upper()))   # "LZA,LZC", '["LZA"]' or ["LZA"]
    uses = {"exam" for c in codes if c in EXAM_CODES} | {"surgical" for c in codes if c in SURGICAL_CODES}
    return uses.pop() if len(uses) == 1 else None


def ai_rows(di: str, extracted: Mapping, observed_at: datetime, prompt_version: str = "v1",
            product_id: str | None = None, sku_id: str | None = None) -> list[EvidenceRow]:
    """AI values fill the gaps, always unverified. "unknown" (or not stated) writes no row."""
    rows = []
    for field, raw in (extracted or {}).items():
        if field not in ATTRIBUTES:
            continue
        v = normalize(field, raw)
        if v is None:
            continue
        attr, unit = ATTRIBUTES[field]
        rows.append(EvidenceRow(di=di, product_id=product_id, sku_id=sku_id, attribute_name=attr,
                                attribute_value=value_str(v), unit=unit, source_type=SourceType.AI_EXTRACTION,
                                source_id=f"ai:{di}:{field}:{prompt_version}", extraction_method=AI_METHOD,
                                verification_status=Verification.UNVERIFIED, observed_at=observed_at))
    return rows


def _key(r: EvidenceRow) -> tuple:
    return (r.di or r.sku_id or r.product_id, r.attribute_name)


def mark_conflicts(rows: list[EvidenceRow]) -> list[EvidenceRow]:
    """Same subject, same attribute, different values (superseded rows aside): every row of that attribute becomes
    conflicting, with the values in the reason. Nothing is dropped and no value is changed."""
    groups: dict[tuple, set[str]] = {}
    for r in rows:
        if r.verification_status != Verification.SUPERSEDED:
            groups.setdefault(_key(r), set()).add(r.attribute_value)
    out = []
    for r in rows:
        values = groups.get(_key(r), set())
        if r.verification_status != Verification.SUPERSEDED and len(values) > 1:
            r = r.model_copy(update={"verification_status": Verification.CONFLICTING,
                                     "reason": f"sources disagree: {', '.join(sorted(values))}"})
        out.append(r)
    return out


def evidence_rows(di: str, gudid_fields: Mapping, extracted: Mapping, observed_at: datetime,
                  prompt_version: str = "v1", product_id: str | None = None,
                  sku_id: str | None = None) -> list[EvidenceRow]:
    """Every evidence row of one GUDID device: structured fields first, AI values second, conflicts marked."""
    rows = gudid_rows(di, gudid_fields, observed_at, product_id, sku_id)
    rows += ai_rows(di, extracted, observed_at, prompt_version, product_id, sku_id)
    return mark_conflicts(rows)


def review_rows(rows: Iterable[EvidenceRow]) -> list[dict]:
    """quote_ai_product.attribute_review: one row per conflicting attribute, with every value and its source."""
    by: dict[tuple, list[EvidenceRow]] = {}
    for r in rows:
        if r.verification_status == Verification.CONFLICTING:
            by.setdefault(_key(r), []).append(r)
    return [{"di": g[0].di, "product_id": g[0].product_id, "sku_id": g[0].sku_id, "attribute_name": g[0].attribute_name,
             "values": "; ".join(f"{r.attribute_value} ({r.source_type.value}:{r.extraction_method})" for r in g),
             "reason": g[0].reason, "observed_at": max(r.observed_at for r in g)} for g in by.values()]


def invariants(rows: Iterable[EvidenceRow], structured: Iterable[EvidenceRow] = ()) -> list[str]:
    """The product facts suite's status checks: zero AI rows verified, zero structured values overwritten (each
    structured value still present, with its value)."""
    rows = list(rows)
    errors = [f"{r.source_id}: ai_extraction marked verified" for r in rows
              if r.source_type == SourceType.AI_EXTRACTION and r.verification_status == Verification.VERIFIED]
    kept = {(r.source_id, r.attribute_value) for r in rows}
    errors += [f"{s.source_id}: structured value {s.attribute_value!r} overwritten or dropped"
               for s in structured if (s.source_id, s.attribute_value) not in kept]
    return errors


def to_domain(rows: Iterable[EvidenceRow]) -> list[Evidence]:
    """The rows the rules read (quote_ai.rules.eligibility): only rows tied to a DBxLab product. Conflicting and
    superseded rows keep their status, so they never count as business truth."""
    return [Evidence(product_id=r.product_id, attribute=r.attribute_name, value=parse_value(r.attribute_value),
                     source_type=r.source_type, source_id=r.source_id, verification=r.verification_status,
                     document_version=r.document_version, verified_by=r.verified_by)
            for r in rows if r.product_id]


def rows_to_domain(rows: Iterable[Mapping]) -> list[Evidence]:
    """Table rows (dicts, as the app reads glove_evidence) -> what the rules read (AI-15)."""
    return to_domain(EvidenceRow.model_validate({k: v for k, v in dict(r).items() if k in EvidenceRow.model_fields})
                     for r in rows)


# ------------------------------------------------------------------ AI-09: documents, versions, the five checks

GOVERNED_CHANNELS = ("supplier_portal", "qa_upload")
AQL_ALLOWED = (0.65, 1.0, 1.5, 2.5, 4.0)
RANGES = {"thickness": (0.03, 0.5, "mm"), "tensile_strength": (7.0, 60.0, "MPa")}
MIL_MM = 0.0254
CHECKS = ("registered", "current", "this_product", "fields", "qa_signoff")


def _norm(s) -> str:
    return "".join(ch for ch in str(s or "").upper() if ch.isalnum())


def current_versions(registry: Iterable[Mapping]) -> list[dict]:
    """is_current per registry row: the latest version of each document ID (then the latest effective date)."""
    rows = [dict(r) for r in registry]
    latest: dict[str, tuple] = {}
    for r in rows:
        k = (int(r["version"]), str(r.get("effective_date") or ""))
        if r["document_id"] not in latest or k > latest[r["document_id"]]:
            latest[r["document_id"]] = k
    for r in rows:
        r["is_current"] = (int(r["version"]), str(r.get("effective_date") or "")) == latest[r["document_id"]]
    return rows


def apply_versions(rows: Iterable[EvidenceRow], registry: Iterable[Mapping]) -> list[EvidenceRow]:
    """Rows from a document version that is no longer current become superseded. Nothing is deleted: the history
    explains every past quote."""
    current = {(r["document_id"], int(r["version"])): r.get("is_current", True)
               for r in current_versions(registry)}
    out = []
    for r in rows:
        if r.document_id and current.get((r.document_id, r.document_version)) is False:
            r = r.model_copy(update={"verification_status": Verification.SUPERSEDED,
                                     "reason": "a newer version of the document is current"})
        out.append(r)
    return out


def parse_quantity(text) -> tuple[float | None, str | None]:
    """"0.08 mm" -> (0.08, "mm"); "3.1 mil" -> (3.1, "mil"); "14 MPa" -> (14.0, "MPa"); "0.08" -> (0.08, None)."""
    if text is None:
        return None, None
    m = re.match(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z%]+)?\s*$", str(text))
    if not m:
        return None, None
    return float(m.group(1)), m.group(2)


def check_value(attribute: str, value: float | None, unit: str | None) -> tuple[float | None, str | None, str]:
    """A spec value with its unit, converted and range-checked: (value, unit, "") or (.., .., the reason it fails).
    A value without a unit is never trusted: "0.08" means nothing without mm or mil."""
    if value is None:
        return None, unit, "no value"
    if attribute == "aql":
        return value, None, "" if value in AQL_ALLOWED else f"AQL {value:g} not in the allowed list"
    lo, hi, want = RANGES[attribute]
    if not unit:
        return value, None, "no unit"
    if attribute == "thickness" and unit.lower() == "mil":
        value, unit = round(value * MIL_MM, 4), "mm"
    if unit.lower() != want.lower():
        return value, unit, f"unit {unit} is not {want}"
    if not lo <= value <= hi:
        return value, want, f"{value:g} {want} outside {lo:g} to {hi:g}"
    return value, want, ""


class Document(BaseModel):
    """A supplier document as the pipeline sees it: its registry entry joined with the values extracted from it."""
    document_id: str
    version: int
    document_type: str               # spec_sheet, test_report, price_list, marketing
    supplier: str | None = None
    products: list[tuple[str, str]] = []      # (supplier, catalog number) pairs the document names
    registered: bool = False         # in document_registry, received through a governed channel
    is_current: bool = False
    product_id: str | None = None    # the DBxLab product it was filed against
    values: dict = {}                # extracted: aql, thickness, tensile_strength, drugs, standard, results


class SkuRef(BaseModel):
    """What the this-product check compares with: the SKU's verified mapping (AI-03)."""
    sku_id: str
    product_id: str
    supplier: str | None
    catalog_no: str | None
    mapping_verified: bool


class Verdict(BaseModel):
    checks: dict[str, bool]
    verified_by: str | None = None
    verified_at: datetime | None = None

    @property
    def verified(self) -> bool:
        return all(self.checks.get(c, False) for c in CHECKS)

    @property
    def failed(self) -> list[str]:
        return [c for c in CHECKS if not self.checks.get(c, False)]


def _this_product(doc: Document, sku: SkuRef) -> bool:
    """The document names this SKU's supplier and catalog number, and the SKU's mapping is verified. A valid report
    for another catalog number proves nothing about our SKU."""
    if not sku.mapping_verified or not sku.catalog_no:
        return False
    return any(_norm(c) == _norm(sku.catalog_no) and (not sku.supplier or _norm(s) == _norm(sku.supplier))
               for s, c in doc.products)


def verify_document(doc: Document, sku: SkuRef, fields_ok: bool, qa: Mapping) -> Verdict:
    """The verification policy (AI-Planning 3.4): five named checks, each recorded. qa: {(document_id, version):
    (verified_by, verified_at)}: a sign-off is per version, so a new version is re-reviewed."""
    signed = qa.get((doc.document_id, doc.version))
    return Verdict(checks={"registered": doc.registered, "current": doc.is_current,
                           "this_product": _this_product(doc, sku), "fields": fields_ok,
                           "qa_signoff": signed is not None},
                   verified_by=signed[0] if signed else None, verified_at=signed[1] if signed else None)


def chemo_fields_ok(doc: Document) -> bool:
    """A chemo claim needs a test report with the drug list, the test standard and a result for every drug. A
    marketing sheet that says "chemo-rated" has none of them."""
    v = doc.values
    drugs, results = v.get("drugs") or [], v.get("results") or {}
    return (doc.document_type == "test_report" and bool(drugs) and bool(v.get("standard"))
            and all(results.get(d) not in (None, "") for d in drugs))


def verify_chemo(doc: Document, sku: SkuRef, qa: Mapping) -> Verdict:
    """Glove A's claim becomes verified only from its own registered, current, complete, signed-off report."""
    verdict = verify_document(doc, sku, chemo_fields_ok(doc), qa)
    if doc.document_type != "test_report":
        verdict.checks["registered"] = False        # registered as a test report, not as marketing
    return verdict


def _row(doc: Document, sku: SkuRef, attr: str, value, unit, verdict: Verdict, reason: str, observed_at: datetime,
         test_method: str | None = None) -> EvidenceRow:
    ok = verdict.verified and not reason
    why = reason or ("" if ok else "failed: " + ", ".join(verdict.failed))
    return EvidenceRow(product_id=sku.product_id, sku_id=sku.sku_id, attribute_name=attr,
                       attribute_value=value_str(value), unit=unit, source_type=SourceType.SUPPLIER_DOCUMENT,
                       source_id=f"{doc.document_id}:v{doc.version}", document_id=doc.document_id,
                       document_version=doc.version, test_method=test_method, extraction_method=DOCUMENT_METHOD,
                       verification_status=Verification.VERIFIED if ok else Verification.UNVERIFIED, reason=why,
                       verified_by=verdict.verified_by if ok else None,
                       verified_at=verdict.verified_at if ok else None, observed_at=observed_at)


def spec_rows(doc: Document, sku: SkuRef, qa: Mapping, observed_at: datetime) -> list[EvidenceRow]:
    """A spec sheet: material and powder-free (AI-15: the contract's evidence for them), then AQL, thickness and
    tensile strength, each with its unit and range check."""
    out = []
    for attr, field in (("material", "material"), ("powder_free", "powder_free")):
        raw = doc.values.get(field)
        value = normalize(field, raw)
        if value is None:
            continue
        problem = "" if not (attr == "material" and value == "other") else f"material {raw!r} not on the list"
        verdict = verify_document(doc, sku, not problem, qa)
        out.append(_row(doc, sku, attr, value, None, verdict, problem, observed_at))
    for attr in ("aql", "thickness", "tensile_strength"):
        raw = doc.values.get(attr)
        if raw is None:
            continue
        value, unit = (float(raw), None) if isinstance(raw, (int, float)) else parse_quantity(raw)
        value, unit, problem = check_value(attr, value, unit)
        if value is None:
            continue
        verdict = verify_document(doc, sku, not problem, qa)
        out.append(_row(doc, sku, attr, value, unit, verdict, problem, observed_at))
    return out


def chemo_rows(doc: Document, sku: SkuRef, qa: Mapping, observed_at: datetime) -> list[EvidenceRow]:
    """A document that says something about chemo testing: one chemo_tested row with its verdict and reason."""
    if doc.document_type not in ("test_report", "marketing"):
        return []
    v = doc.values
    if not (v.get("drugs") or v.get("standard") or v.get("chemo_claim")):
        return []
    verdict = verify_chemo(doc, sku, qa)
    return [_row(doc, sku, "chemo_tested", True, None, verdict, "", observed_at, test_method=v.get("standard"))]


def document_evidence(documents: Iterable[Document], skus: Iterable[SkuRef], qa: Mapping,
                      observed_at: datetime) -> list[EvidenceRow]:
    """supplier_quality_evidence: every document, for every SKU of the product it was filed against, then versions
    applied (an older version's rows superseded). Only this function decides a status; the model only extracts."""
    by_product: dict[str, list[SkuRef]] = {}
    for s in skus:
        by_product.setdefault(s.product_id, []).append(s)
    docs = list(documents)
    rows: list[EvidenceRow] = []
    for d in docs:
        for sku in by_product.get(d.product_id or "", []):
            if d.document_type == "spec_sheet":
                rows += spec_rows(d, sku, qa, observed_at)
            rows += chemo_rows(d, sku, qa, observed_at)
    registry = [{"document_id": d.document_id, "version": d.version} for d in docs]
    out = []
    for r in apply_versions(rows, registry):
        stale = any(d.document_id == r.document_id and d.version == r.document_version and not d.is_current
                    for d in docs)
        if stale and r.verification_status != Verification.SUPERSEDED:
            r = r.model_copy(update={"verification_status": Verification.SUPERSEDED,
                                     "reason": "a newer version of the document is current", "verified_by": None,
                                     "verified_at": None})
        out.append(r)
    return out


# ------------------------------------------------------------------ from the pipeline's rows to Documents


def _list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    s = str(v).strip()
    if s.startswith("["):
        try:
            import json
            return _list(json.loads(s))
        except ValueError:
            pass
    return [x.strip() for x in re.split(r"[,;\n]", s) if x.strip()]


def _results(v) -> dict[str, str]:
    """{"Carmustine": "45 min"} from a dict, a JSON string, or "Carmustine: 45 min; Cisplatin: >240 min"."""
    if isinstance(v, Mapping):
        return {str(k).strip(): str(x).strip() for k, x in v.items()}
    s = str(v or "").strip()
    if s.startswith("{"):
        try:
            import json
            return _results(json.loads(s))
        except ValueError:
            pass
    out = {}
    for part in re.split(r"[;\n]", s):
        if ":" in part:
            k, x = part.split(":", 1)
            if k.strip():
                out[k.strip()] = x.strip()
    return out


def parse_extracted(raw: Mapping | None) -> dict:
    """The strings an AI Function returned -> typed values: lists, a result per drug, numbers with their units."""
    raw = dict(raw or {})
    out = {k: raw.get(k) for k in ("standard", "chemo_claim", "material", "powder_free")}
    if raw.get("aql") not in (None, ""):
        try:
            out["aql"] = float(str(raw["aql"]).strip())
        except ValueError:
            out["aql"] = None
    for k in ("thickness", "tensile_strength"):
        if raw.get(k) not in (None, ""):
            out[k] = str(raw[k]).strip()
    out["drugs"] = _list(raw.get("drugs") or raw.get("chemo_drugs"))
    out["results"] = _results(raw.get("results"))
    out["catalog_numbers"] = _list(raw.get("catalog_numbers"))
    return {k: v for k, v in out.items() if v not in (None, "", [], {})}


def to_document(registry_row: Mapping, extracted: Mapping | None, registry: Iterable[Mapping]) -> Document:
    """A registry row (how the document was received and filed) joined with what was read from it. The products it
    names are what the document itself says (extracted catalog numbers); the registry decides registered/current."""
    values = parse_extracted(extracted)
    current = {(r["document_id"], int(r["version"])): r["is_current"] for r in current_versions(registry)}
    supplier = str(registry_row.get("supplier") or "")
    cats = values.get("catalog_numbers") or _list(registry_row.get("catalog_no"))
    return Document(document_id=registry_row["document_id"], version=int(registry_row["version"]),
                    document_type=registry_row["document_type"], supplier=supplier,
                    products=[(supplier, c) for c in cats],
                    registered=registry_row.get("received_via") in GOVERNED_CHANNELS,
                    is_current=current.get((registry_row["document_id"], int(registry_row["version"])), False),
                    product_id=registry_row.get("product_id"), values=values)
