"""Compare and explain (AI-16): the table comes from data, every statement has a citation and a truth layer, and a
"business truth" label always points to verified evidence. The Comparison suite's deterministic assertion."""
import json
from types import SimpleNamespace

from quote_ai.llm import knowledge_assistant as ka
from quote_ai.llm import prompts, rag
from quote_ai.mock import catalog, knowledge
from quote_ai.product import compare
from quote_ai.search import chunks, local, retriever
from quote_ai.storage.evidence_store import DemoEvidenceStore
from quote_ai.storage.product_catalog import DemoProductCatalog

STORE = DemoEvidenceStore()
CAT = DemoProductCatalog()
ELIGIBLE = ["GLOVE-A", "GLOVE-C", "GLOVE-D"]
CANDIDATES = [{"product_id": "GLOVE-A", "status": "eligible", "label": "on time", "failed": []},
              {"product_id": "GLOVE-C", "status": "eligible", "label": "alternative, needs acceptance", "failed": []},
              {"product_id": "GLOVE-D", "status": "eligible", "label": "on time", "failed": []},
              {"product_id": "GLOVE-B", "status": "needs_evidence", "label": None, "failed": ["chemo_tested"]},
              {"product_id": "GLOVE-E", "status": "excluded", "label": None, "failed": ["latex_free"]}]
NAMES = {p["product_id"]: p["name"] for p in catalog.products()}
ROWS = STORE.for_products(ELIGIBLE + ["GLOVE-B"])
TABLE = compare.comparison_table(ELIGIBLE, NAMES, ROWS, CANDIDATES, CAT.price_book(),
                                 CAT.stock([f"GLOVE-{l}-{s}" for l in "ACD" for s in catalog.SIZES])[0], 8.00)
CHUNKS = knowledge.demo_chunks(STORE.rows)
INDEX = local.InMemoryIndex(CHUNKS, text_column="text", primary_key="chunk_id", keyword_columns=())


def retrieve(q, scope, k, accounts):
    return retriever.retrieve_chunks(INDEX, q, scope, k, accounts, chunking="section")


def test_every_table_value_equals_its_evidence_row():
    for col in TABLE["columns"] + TABLE["needs_evidence"]:
        for attr, cell in col["specs"].items():
            if cell["status"] == "missing":
                assert not [r for r in ROWS if r["product_id"] == col["product_id"] and r["attribute_name"] == attr
                            and r["verification_status"] in ("verified", "unverified", "conflicting")]
                continue
            row = next(r for r in ROWS if r["product_id"] == col["product_id"] and r["source_id"] == cell["source_id"]
                       and r["attribute_name"] == attr)
            assert (cell["value"], cell["unit"], cell["status"]) == \
                (row["attribute_value"], row["unit"], row["verification_status"])


def test_a_verified_value_wins_and_an_unverified_one_is_shown_as_unverified():
    a = next(c for c in TABLE["columns"] if c["product_id"] == "GLOVE-A")
    assert a["specs"]["chemo_tested"]["status"] == "verified" and a["specs"]["chemo_tested"]["source_id"] == "RPT-CHEMO-A:v1"
    assert a["specs"]["length"]["status"] == "unverified"                    # AI-read from the description: amber
    assert "unverified" in compare.table_markdown(TABLE)
    b, = TABLE["needs_evidence"]
    assert b["product_id"] == "GLOVE-B" and b["specs"]["chemo_tested"]["status"] != "verified"


def test_no_quality_score_column_and_statuses_from_the_rules():
    text = json.dumps(TABLE).lower()
    assert "score" not in text and "rating" not in text
    offers = {c["product_id"]: c["offer"] for c in TABLE["columns"]}
    assert offers == {"GLOVE-A": "on time", "GLOVE-C": "alternative, needs acceptance", "GLOVE-D": "on time"}
    assert [c["product_id"] for c in TABLE["columns"]] == ELIGIBLE                 # B and E are not options
    a = TABLE["columns"][0]
    assert a["commercial"]["list_price"] == 8.60 and a["fit"]["price_vs_target"] == 0.60
    assert a["completeness"] == {"verified": 8, "required": 8}


def test_section_chunks_keep_their_title_and_their_layer():
    rpt = [c for c in CHUNKS if c["doc_id"] == "RPT-CHEMO-A" and c["chunking"] == "section"]
    drugs = next(c for c in rpt if c["section"].startswith("Drugs tested"))
    assert drugs["text"].startswith("Drugs tested") and "Carmustine: 45 min" in drugs["text"]
    assert {c["truth_layer"] for c in rpt} == {"business_truth"}
    assert {c["truth_layer"] for c in CHUNKS if c["doc_id"] in ("MKT-B", "GUDID-DESC-B")} == {"extracted_claim"}
    assert {c["account_id"] for c in CHUNKS if c["doc_id"] == "PS-ACC-001"} == {"ACC-001"}
    fixed = chunks.chunk_fixed("D", 1, " ".join(["w"] * 1200))
    assert [c["chunk_id"] for c in fixed] == ["D:1:f0", "D:1:f1", "D:1:f2"]


def assert_grounded(answer):
    """The Comparison suite's assertion: a citation and a layer per statement; business truth -> verified."""
    verified_docs = {(r["document_id"], int(r["document_version"])) for r in STORE.rows
                     if r.get("document_id") and r["verification_status"] == "verified"}
    for s in answer["statements"]:
        assert s["citation"] and s["layer"] in rag.LAYERS, s
        if s["layer"] == "business_truth":
            if s["citation"].startswith("table:"):
                _, pid, attr = s["citation"].split(":")
                assert compare.cell_layer(TABLE, pid, attr) == "business_truth"
            else:
                doc, version, _ = s["citation"].split(":")
                assert (doc, int(version)) in verified_docs


def test_glove_a_chemo_claim_is_business_truth_from_its_report():
    r = rag.explain_comparison("What supports the chemo claim?", ["GLOVE-A"], TABLE, retrieve)
    assert_grounded(r)
    assert any(s["layer"] == "business_truth" and "RPT-CHEMO-A" in s["citation"] + s["text"] for s in r["statements"])


def test_glove_b_its_description_says_chemo_tested_extracted_claim_and_no_report_on_file():
    r = rag.explain_comparison("Can we offer it for chemo use?", ["GLOVE-B"], TABLE, retrieve)
    assert_grounded(r)
    assert any(s["layer"] == "extracted_claim" and s["citation"].startswith("GUDID-DESC-B") for s in r["statements"])
    assert any(s["citation"] == "rule:GLOVE-B:chemo_tested" and "no chemo test report on file" in s["text"]
               for s in r["statements"])
    assert not any(s["layer"] == "business_truth" and "GLOVE-B" in s["citation"] for s in r["statements"])


def test_something_not_in_the_documents_is_no_evidence_on_file():
    r = rag.explain_comparison("What is the shelf life in tropical storage?", ["GLOVE-A"], TABLE, retrieve)
    assert r["answer"] == rag.NO_EVIDENCE and r["statements"] == []


def test_explain_the_differences_cites_every_statement():
    r = rag.explain_comparison("Explain the differences for the customer", ELIGIBLE, TABLE, retrieve)
    assert r["statements"] and all(s["citation"].split(":")[1] in ELIGIBLE or not s["citation"].startswith("table:")
                                   for s in r["statements"])
    assert_grounded(r)


def test_ground_drops_what_was_not_retrieved_and_relabels_from_the_source():
    chunk = next(c for c in CHUNKS if c["doc_id"] == "GUDID-DESC-B" and c["chunking"] == "section")
    kept, problems = rag.ground([
        {"text": "Glove B is chemo-tested and suitable for oncology wards.", "citation": "", "layer": "business_truth"},
        {"text": "Glove B is chemo-tested.", "citation": chunk["chunk_id"], "layer": "business_truth"},
        {"text": "invented", "citation": "RPT-CHEMO-B:1:2", "layer": "business_truth"},
    ], [chunk], TABLE)
    assert [s["layer"] for s in kept] == ["extracted_claim"] and len(problems) == 3


def test_a_model_answer_goes_through_the_same_checks():
    def model(prompt):
        assert "Use only the comparison table and the context chunks" in prompt and "[GUDID-DESC-B:1:0]" in prompt
        return json.dumps({"statements": [
            {"text": "Its description says it is tested for chemotherapy drugs.", "citation": "GUDID-DESC-B:1:0",
             "layer": "extracted_claim"},
            {"text": "Glove B is chemo-tested.", "citation": "RPT-CHEMO-B:1:2", "layer": "business_truth"}]})
    r = rag.explain_comparison("Can we offer it for chemo use?", ["GLOVE-B"], TABLE, retrieve, generate=model)
    assert [s["citation"] for s in r["statements"]] == ["GUDID-DESC-B:1:0", "rule:GLOVE-B:chemo_tested"]
    assert r["problems"] and "RPT-CHEMO-B" in r["problems"][0]


def test_the_compare_prompt_is_registered_with_its_rules():
    template, _ = prompts.PROMPTS["compare_explain"]["v1"]
    for rule in ("no evidence on file", "citation", "business_truth", "extracted_claim", "{{context}}", "{{table}}"):
        assert rule in template


def test_knowledge_assistant_answer_becomes_statements_with_our_layers():
    resp = SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(
        text="Glove A was tested to ASTM D6978. Carmustine breakthrough is 45 minutes.",
        annotations=[SimpleNamespace(filename="/Volumes/c/s/v/test_reports/RPT-CHEMO-A_v1.pdf")])])])
    st = ka.to_statements(resp, CHUNKS)
    assert len(st) == 2 and all(s["layer"] == "business_truth" and s["citation"].startswith("RPT-CHEMO-A:1:")
                                for s in st)
    bare = SimpleNamespace(output=[SimpleNamespace(content=[SimpleNamespace(text="Probably fine.", annotations=[])])])
    assert ka.to_statements(bare, CHUNKS) == [{"text": "Probably fine.", "citation": "", "layer": ""}]
