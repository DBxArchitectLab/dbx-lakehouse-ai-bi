-- Knowledge chunks and rep accounts (AI-16, AI-17). {ai} and {sales} are catalog.schema from the bundle.
-- Run by src/quote_ai/jobs/knowledge_job.py setup (the quote_ai_knowledge_index job).

CREATE TABLE IF NOT EXISTS {ai}.knowledge_chunks (
  chunk_id     STRING NOT NULL COMMENT '<doc_id>:<version>:<section> (fixed chunks: <doc_id>:<version>:f<n>); primary key of knowledge_index',
  doc_id       STRING COMMENT 'Supplier document, GUDID description, playbook, GMDN notes or a price sheet',
  doc_version  INT    COMMENT 'Document version',
  product_id   STRING COMMENT 'The DBxLab product it is about; * for general text',
  sku_ids      ARRAY<STRING> COMMENT 'The SKUs of that product',
  account_id   STRING COMMENT 'public, or the customer account of a customer-confidential document: filtered before retrieval',
  section      STRING COMMENT 'Section title (kept inside the text too)',
  truth_layer  STRING COMMENT 'business_truth (verified supplier document), extracted_claim (description, marketing, unverified) or source_truth (playbook, notes, price sheets)',
  chunking     STRING COMMENT 'section (AI-16) or fixed500 (the AI-17 baseline)',
  text         STRING COMMENT 'The chunk text: what is embedded and quoted'
) COMMENT 'Knowledge chunks for compare and explain (AI-16), demo data: the source of the AI Search index knowledge_index. Every chunk carries its truth layer and its account.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);

CREATE TABLE IF NOT EXISTS {sales}.rep_account (
  rep_id      STRING NOT NULL COMMENT 'Sign-in name of a rep (the part of the email before @)',
  account_id  STRING NOT NULL COMMENT 'A customer account the rep owns'
) COMMENT 'Which customer accounts each rep owns (demo data, AI-17): the account filter of knowledge retrieval, taken from the signed-in identity.';

ALTER TABLE {ai}.knowledge_chunks SET TAGS ('app' = 'quote_ai', 'demo_data' = 'true');
ALTER TABLE {sales}.rep_account SET TAGS ('app' = 'quote_ai', 'demo_data' = 'true');
