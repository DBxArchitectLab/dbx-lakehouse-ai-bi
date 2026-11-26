# AI-09: Document intelligence, supplier quality evidence

Supplier spec sheets and test reports (synthetic PDFs) become quality evidence with a document ID and version on every
value. **Extraction never verifies:** a value becomes verified only when five recorded checks pass, and an older
document version's rows become superseded, never deleted. Glove A's chemo claim is verified from its own signed-off
report; Glove B has none and stays an extracted claim.

| File | What |
| --- | --- |
| `src/quote_ai/mock/documents.py` | Synthetic spec sheets, test reports, a marketing sheet, price lists and the Lakeside request as PDFs, with their truth; the demo's drops as scenarios |
| `src/quote_ai/pipelines/documents.py` | Auto Loader (binaryFile) -> `ai_parse_document` -> `ai_extract` -> `supplier_quality_evidence` |
| `src/quote_ai/product/evidence.py` | + `current_versions`, `apply_versions`, `check_value` (units, ranges), `verify_document`, `verify_chemo`, `document_evidence` |
| `src/quote_ai/product/documents.sql` | `document_registry`, `document_qa_signoff`, `quote_ai_sales.supplier_price_list` |
| `src/quote_ai/product/evidence_views.sql` | `glove_evidence` (AI-08 + AI-09 evidence), `product_documents` |
| `src/quote_ai/jobs/documents_job.py` | setup, generate (+ register), signoff, views |
| `src/quote_ai/llm/prompts.py` | + `extract_spec_sheet` v1, `extract_test_report` v1 |
| `resources/quote_ai/quote_ai_documents.pipeline.yml` | The pipeline |
| `resources/quote_ai/quote_ai_mock_documents.job.yml` | `quote_ai_mock_documents` (generate, pipeline, views) and `quote_ai_document_signoff` |
| `evaluation/quote_ai/v1/product_facts/documents.jsonl` | The documents' truth values (written by the generator) |
| `src/quote_ai/app/backend/routers/documents.py` | `POST /api/documents/request` (a PDF), `GET /api/documents/{product}` |
| `src/quote_ai/app/frontend/src/pages/NewRequest.tsx`, `Product.tsx` | PDF upload; the Documents tab |
| `tests/quote_ai/test_document_evidence.py` | One test per check, versions, units, ranges, the generator self-test |

## The five checks (AI-Planning 3.4)

| Check | Passes when |
| --- | --- |
| registered | in `document_registry`, received through a governed channel, registered as a test report (a marketing sheet is not) |
| current | the latest version of the document ID |
| this_product | the document names the SKU's catalog number, and the SKU's mapping (AI-03) is verified |
| fields | test report: drug list, standard and a result per drug; spec sheet: a unit and a value in range |
| qa_signoff | a QA reviewer signed this version off (`document_qa_signoff`); a new version is reviewed again |

All pass: verified, with `verified_by` and `verified_at`. Any fails: unverified, with the failing checks in `reason`.
The mapping carries no supplier name yet, so `this_product` compares catalog numbers; the supplier joins when
DBxLab's supplier master arrives.

## Demo, step by step

```
databricks bundle run quote_ai_mock_documents -t dev                                  # specs, C and D reports, MKT-B
databricks bundle run quote_ai_mock_documents -t dev --params scenarios=glove_a_report   # 4 checks pass, no sign-off
databricks bundle run quote_ai_document_signoff -t dev --params document_id=RPT-CHEMO-A,version=1,reviewer=<you>
databricks bundle run quote_ai_mock_documents -t dev --params scenarios=glove_a_report,other_catalog_report
databricks bundle run quote_ai_mock_documents -t dev --params scenarios=glove_a_report,other_catalog_report,spec_v4
python evaluation/quote_ai/run_eval.py --suite product_facts --target dev --document-values values.json
```

`values.json` is `{path: v}` exported from `quote_ai_product.document_values`. Spec sheets and the reports already on
file (Gloves C and D) are signed off by `demo:qa` when generated (demo data only); Glove A's report is signed off on
camera. The SPEC-A v4 drop is signed off by `demo:qa` too, so its rows show as verified right away (the draft's
"v4 rows verified"); in real use a person reviews every new version.

## Method comparison (SQL path vs Information Extraction)

Measure both on the same documents: the SQL path is this pipeline; Agent Bricks Information Extraction is created in
the UI with the same labels, run on the same folder, and its output exported in the same `{path: v}` form, so
`score_documents` scores both. Keep the cheaper method where accuracy is equal. The price lists
(`supplier_price_list`) are where Information Extraction is tried first; their truth is in `documents.jsonl`
(`prices`) and they are scored apart.

## Verify before recording

- `ai_parse_document`: output structure (`PARSED_TEXT_SQL` in `evidence.py` is the one place that reads it), file
  types, region availability; `read_files(..., format => 'binaryFile')` on a single file for the app's upload.
- Agent Bricks Information Extraction: name, availability on Azure in our region, how it is created and called.
- cloudFiles with binaryFile in a serverless pipeline.
- The app's `WRITE_VOLUME` resource and `WorkspaceClient().files.upload` as the app's service principal.
- On screen: supplier documents are synthetic demo data; sales quoting only, not clinical.
