-- Supplier documents (AI-09): the registry of what was received, and the QA sign-offs. Written by
-- src/quote_ai/jobs/documents_job.py (register, signoff); read by the quote_ai_documents pipeline, which writes
-- supplier_quality_evidence. All documents are synthetic demo data.
-- {product} and {sales} are catalog.schema, filled in from the bundle's schema names (quote_ai.storage.sql_files).

CREATE TABLE IF NOT EXISTS {product}.document_registry (
  document_id    STRING NOT NULL COMMENT 'Supplier document ID, e.g. SPEC-A or RPT-CHEMO-A',
  document_type  STRING COMMENT 'spec_sheet, test_report, marketing, price_list or request',
  supplier       STRING COMMENT 'Supplier named on the document',
  catalog_no     STRING COMMENT 'Catalog numbers the document covers, comma-separated, as received',
  product_id     STRING COMMENT 'The DBxLab product the document was filed against',
  sku_id         STRING COMMENT 'The DBxLab SKU, when the document is about one SKU only',
  version        INT    COMMENT 'Document version; the latest is current, older ones are superseded',
  effective_date DATE   COMMENT 'When this version took effect',
  path           STRING COMMENT 'The file in the quote_ai_documents volume',
  checksum       STRING COMMENT 'SHA-256 of the file as received: a changed file is a new version, never an edit',
  is_current     BOOLEAN COMMENT 'Whether this is the latest version of the document',
  received_via   STRING COMMENT 'supplier_portal or qa_upload are governed channels; anything else is not registered evidence',
  registered_at  TIMESTAMP COMMENT 'When it was registered'
) COMMENT 'Supplier documents as received (demo data): one row per document version. Check 1 and 2 of the verification policy (registered, current).';

CREATE TABLE IF NOT EXISTS {product}.document_qa_signoff (
  document_id      STRING NOT NULL COMMENT 'The document',
  document_version INT    COMMENT 'The version signed off; a new version is reviewed again',
  verified_by      STRING COMMENT 'The QA reviewer',
  verified_at      TIMESTAMP COMMENT 'When'
) COMMENT 'QA sign-offs of supplier documents, one per document version: check 5 of the verification policy. Written by a person (documents_job.py signoff), never by a model.';

CREATE TABLE IF NOT EXISTS {sales}.supplier_price_list (
  document_id      STRING NOT NULL COMMENT 'The price list document',
  document_version INT    COMMENT 'Its version',
  catalog_no       STRING COMMENT 'Supplier catalog number',
  unit_price       DECIMAL(10,2) COMMENT 'Supplier price per box, USD (demo data)',
  extraction_method STRING COMMENT 'information_extraction or ai_extract: the method chosen by measurement',
  observed_at      TIMESTAMP COMMENT 'When it was read'
) COMMENT 'Supplier prices read from supplier price lists (demo data). Input to cost, never to a customer price directly.';

ALTER TABLE {product}.document_registry SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {product}.document_qa_signoff SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {sales}.supplier_price_list SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
