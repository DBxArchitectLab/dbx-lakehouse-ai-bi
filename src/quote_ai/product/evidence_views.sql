-- One place to read every piece of glove evidence (AI-09): product_evidence (AI-08, GUDID fields and AI values) and
-- supplier_quality_evidence (AI-09, supplier documents), with the same columns. What the app and the rules read.
-- Created after both pipelines have run (src/quote_ai/jobs/documents_job.py views). {product} from the bundle.

CREATE OR REPLACE VIEW {product}.glove_evidence
COMMENT 'Every glove attribute claim with its source, method and status: GUDID fields and AI values (product_evidence) and supplier documents (supplier_quality_evidence). Only verified rows are business truth.'
AS SELECT di, product_id, sku_id, attribute_name, attribute_value, unit, source_type, source_id, document_id,
          document_version, test_method, extraction_method, verification_status, reason, verified_by, verified_at,
          observed_at
FROM {product}.product_evidence
UNION ALL
SELECT di, product_id, sku_id, attribute_name, attribute_value, unit, source_type, source_id, document_id,
       document_version, test_method, extraction_method, verification_status, reason, verified_by, verified_at,
       observed_at
FROM {product}.supplier_quality_evidence;

CREATE OR REPLACE VIEW {product}.product_documents
COMMENT 'Supplier documents per DBxLab product with their version and status, for the app''s Documents tab (demo data).'
AS SELECT r.product_id, r.document_id, r.document_type, r.version, r.effective_date, r.is_current, r.received_via,
          s.verified_by, s.verified_at
FROM {product}.document_registry r
LEFT JOIN {product}.document_qa_signoff s ON s.document_id = r.document_id AND s.document_version = r.version
WHERE r.product_id IS NOT NULL;
