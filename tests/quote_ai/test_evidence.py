"""Evidence with precedence (AI-08): AI-extracted is never verified, a structured GUDID value is never overwritten,
two sources that disagree are both conflicting. No Databricks connection."""
from datetime import datetime, timezone

import pytest

import lakeside_fixtures as fx
from quote_ai.domain.models import Eligibility, SourceType, Verification
from quote_ai.llm import attributes, prompts
from quote_ai.product import evidence as ev
from quote_ai.rules.eligibility import check_product

AT = datetime(2026, 11, 20, tzinfo=timezone.utc)
CHEMO = "Nitrile exam glove, powder-free, textured fingertips, 9.5 in, tested for use with chemotherapy drugs"
GLOVE_A_EXTRACTED = {"material": "nitrile", "powder_free": "true", "textured": "true", "length_in": "9.5",
                     "chemo_claim": "true", "use_type": "exam", "latex_free": None}
LATEX_FREE_LABEL = {"labeled_no_nrl": "true", "labeled_contains_nrl": "false", "device_sterile": "false",
                    "in_commercial_distribution": "true"}


def rows(extracted=GLOVE_A_EXTRACTED, fields=LATEX_FREE_LABEL, di="00812345000017"):
    return ev.evidence_rows(di, fields, extracted, AT, product_id="GLOVE-A", sku_id="GLOVE-A-M")


def by_attr(rs):
    out = {}
    for r in rs:
        out.setdefault(r.attribute_name, []).append(r)
    return out


def test_glove_a_chemo_claim_is_an_unverified_ai_value_and_latex_free_stays_verified():
    a = by_attr(rows())
    chemo, = a["chemo_tested"]
    assert (chemo.attribute_value, chemo.source_type, chemo.extraction_method, chemo.verification_status) == \
        ("true", SourceType.AI_EXTRACTION, "ai_function", Verification.UNVERIFIED)
    latex, = a["latex_free"]
    assert (latex.attribute_value, latex.source_type, latex.extraction_method, latex.verification_status) == \
        ("true", SourceType.GUDID, "structured_field", Verification.VERIFIED)
    assert a["length"][0].unit == "in" and a["length"][0].attribute_value == "9.5"


def test_glove_b_same_sentence_same_row_same_status():
    a = by_attr(rows())["chemo_tested"][0]
    b = by_attr(rows(di="00812345000024"))["chemo_tested"][0]
    assert (a.attribute_value, a.verification_status) == (b.attribute_value, b.verification_status)


def test_no_ai_extraction_row_is_ever_verified():
    with pytest.raises(ValueError, match="never verified"):
        ev.EvidenceRow(attribute_name="chemo_tested", attribute_value="true", source_type=SourceType.AI_EXTRACTION,
                       source_id="ai:x", extraction_method="ai_function", verification_status=Verification.VERIFIED,
                       observed_at=AT)
    assert all(r.verification_status != Verification.VERIFIED for r in rows()
               if r.source_type == SourceType.AI_EXTRACTION)


def test_a_structured_latex_value_is_never_overwritten_and_a_disagreement_is_conflicting_on_both():
    # the description says latex-free; the GUDID field says it contains natural rubber latex
    fields = {"labeled_contains_nrl": "true", "labeled_no_nrl": "false"}
    rs = rows(GLOVE_A_EXTRACTED | {"latex_free": "true"}, fields)
    latex = by_attr(rs)["latex_free"]
    assert {(r.source_type, r.attribute_value) for r in latex} == {(SourceType.GUDID, "false"),
                                                                    (SourceType.AI_EXTRACTION, "true")}
    assert {r.verification_status for r in latex} == {Verification.CONFLICTING}
    structured = ev.gudid_rows("00812345000017", fields, AT)
    assert ev.invariants(rs, structured) == []
    review, = ev.review_rows(rs)
    assert review["attribute_name"] == "latex_free" and "gudid" in review["values"]


def test_agreeing_sources_are_not_conflicting():
    rs = rows(GLOVE_A_EXTRACTED | {"latex_free": "true"})
    assert {r.verification_status for r in by_attr(rs)["latex_free"]} == {Verification.VERIFIED,
                                                                          Verification.UNVERIFIED}


def test_values_outside_the_allowed_list_map_to_other_or_are_dropped():
    assert ev.normalize("material", "blue nitrile blend") == "other"
    assert ev.normalize("material", "NITRILE") == "nitrile"
    assert ev.normalize("use_type", "examination") == "exam"
    assert ev.normalize("length_in", "95") is None                # a misread, not a glove
    assert ev.normalize("powder_free", "maybe") is None


def test_unknown_from_the_model_writes_no_evidence_row():
    rs = ev.ai_rows("di", {"material": "unknown", "textured": None, "chemo_claim": "", "other_field": "x"}, AT)
    assert rs == []


def test_invariants_catch_a_dropped_structured_value():
    structured = ev.gudid_rows("di", LATEX_FREE_LABEL, AT)
    assert ev.invariants([], structured)


def test_ai_evidence_alone_never_makes_glove_a_eligible():
    """The rules read the rows: an unverified chemo claim is unknown, never a match (AI-15 decides on this)."""
    product = fx.product("A")
    base = [e for e in fx.evidence() if e.product_id == "GLOVE-A" and e.attribute != "chemo_tested"]
    domain = ev.to_domain(rows())
    res = check_product(product, fx.lakeside_r1(), base + domain, "ev-test")
    assert res.status == Eligibility.NEEDS_EVIDENCE
    assert next(c for c in res.checks if c.requirement == "chemo_tested").result.value == "unknown"


def test_the_attribute_form_and_prompt_agree_with_the_evidence_vocabulary():
    assert list(attributes.AttributeForm.model_fields) == ev.EXTRACT_FIELDS
    assert "'" not in attributes.response_format_sql().replace("''", "")
    assert "{{description}}" in prompts.ATTRIBUTES_V1 and prompts.PROMPTS["extract_glove_attributes"]["v1"]
    assert prompts.fill(prompts.ATTRIBUTES_V1, description=CHEMO).endswith(f"<description>{CHEMO}</description>")


def test_extract_attributes_validates_the_answer():
    import json
    from types import SimpleNamespace

    from quote_ai.llm.client import Reply
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
    client_answer = json.dumps({"material": "nitrile", "powder_free": True, "textured": True, "length_in": 9.5,
                                "chemo_claim": True, "use_type": "exam", "latex_free": None})

    def create(**kw):
        assert kw["response_format"]["type"] == "json_schema"
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=client_answer))], usage=usage)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert attributes.extract_attributes(CHEMO, "ep", client)["chemo_claim"] is True
    assert Reply  # the client module's reply type is what chat returns
