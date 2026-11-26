-- Search and eligibility tables (AI-15). {product} and {ai} are catalog.schema from the bundle.
-- Run by src/quote_ai/jobs/search_job.py setup (the quote_ai_search_index job), after the product model and the
-- evidence views (quote_ai_mock_documents views). Demo data: the wider range is synthetic (quote_ai.mock.range).

CREATE TABLE IF NOT EXISTS {product}.dbxlab_range (
  sku_id              STRING NOT NULL COMMENT 'Range SKU, e.g. GLOVE-G-S',
  product_id          STRING NOT NULL COMMENT 'Range product family, GLOVE-G to GLOVE-R',
  product_name        STRING COMMENT 'Name shown to reps',
  catalog_number      STRING COMMENT 'DBxLab catalog number, e.g. DBX-NS-S-100',
  size                STRING COMMENT 'XS, S, M, L, XL or XXL',
  material            STRING COMMENT 'nitrile, latex, vinyl, neoprene or polyisoprene',
  intended_use        STRING COMMENT 'exam or surgical',
  unit                STRING COMMENT 'The selling unit: box',
  gloves_per_unit     INT    COMMENT 'Gloves in one selling unit',
  sellable            BOOLEAN COMMENT 'Whether DBxLab sells the SKU',
  mapping_verified    BOOLEAN COMMENT 'Demo binding: counted as a verified mapping',
  device_description  STRING COMMENT 'The synthetic GUDID-like description',
  demo_data           BOOLEAN COMMENT 'Always true'
) COMMENT 'DBxLab wider glove range (demo data, AI-15): catalog-only families G to R that make search meaningful. Not in the price book or inventory, so never priced.';

CREATE TABLE IF NOT EXISTS {product}.range_evidence (
  di                  STRING COMMENT 'Synthetic device identifier',
  product_id          STRING COMMENT 'Range product',
  sku_id              STRING COMMENT 'Range SKU',
  attribute_name      STRING COMMENT 'The attribute',
  attribute_value     STRING COMMENT 'Its value',
  unit                STRING COMMENT 'Unit',
  source_type         STRING COMMENT 'gudid, supplier_document or ai_extraction',
  source_id           STRING COMMENT 'Where it came from',
  document_id         STRING COMMENT 'Supplier document',
  document_version    INT    COMMENT 'Its version',
  test_method         STRING COMMENT 'Test standard',
  extraction_method   STRING COMMENT 'How it was read',
  verification_status STRING COMMENT 'verified, unverified, conflicting or superseded',
  reason              STRING COMMENT 'Why',
  verified_by         STRING COMMENT 'Reviewer (demo:qa)',
  verified_at         TIMESTAMP COMMENT 'When',
  observed_at         TIMESTAMP COMMENT 'When observed'
) COMMENT 'Evidence of the wider glove range (demo data, AI-15), with the same columns as glove_evidence. Only verified rows are business truth.';

CREATE OR REPLACE VIEW {product}.catalog_skus
COMMENT 'Every SKU the eligibility engine can check: DBxLab SKUs (mapping verified when a verified mapping has the SKU glove count) and the wider range (demo data).'
AS SELECT s.sku_id, s.product_id, p.name AS product_name, s.catalog_number, s.size, s.unit, s.gloves_per_unit,
          s.sellable,
          EXISTS(SELECT 1 FROM {product}.product_device_mapping m WHERE m.sku_id = s.sku_id
                 AND m.verification_status = 'verified' AND m.gloves_per_unit = s.gloves_per_unit) AS mapping_verified,
          p.material, p.intended_use
FROM {product}.dbxlab_sku s JOIN {product}.dbxlab_product p ON p.product_id = s.product_id
UNION ALL
SELECT sku_id, product_id, product_name, catalog_number, size, unit, gloves_per_unit, sellable, mapping_verified,
       material, intended_use
FROM {product}.dbxlab_range;

CREATE OR REPLACE VIEW {product}.eligibility_evidence
COMMENT 'What the eligibility engine reads: glove_evidence (AI-08, AI-09) and the wider range evidence. Only verified rows are business truth.'
AS SELECT di, product_id, sku_id, attribute_name, attribute_value, unit, source_type, source_id, document_id,
          document_version, test_method, extraction_method, verification_status, reason, verified_by, verified_at,
          observed_at
FROM {product}.glove_evidence
UNION ALL
SELECT di, product_id, sku_id, attribute_name, attribute_value, unit, source_type, source_id, document_id,
       document_version, test_method, extraction_method, verification_status, reason, verified_by, verified_at,
       observed_at
FROM {product}.range_evidence;

CREATE TABLE IF NOT EXISTS {ai}.sku_search_doc (
  sku_id                      STRING NOT NULL COMMENT 'The SKU (primary key of the index)',
  product_id                  STRING COMMENT 'Its product family',
  catalog_number              STRING COMMENT 'DBxLab catalog number: matched exactly by hybrid search',
  size                        STRING COMMENT 'Size: a pre-filter, never safety',
  material_family             STRING COMMENT 'Material as DBxLab sells it: a pre-filter',
  in_commercial_distribution  BOOLEAN COMMENT 'GUDID distribution status: a pre-filter; the engine checks it again',
  search_text                 STRING COMMENT 'Product name, brand, GUDID description, evidence summary: what is embedded'
) COMMENT 'The SKU search document (AI-15): one row per sellable SKU, the source of the AI Search index sku_search_index. Finds candidates; never decides eligibility.'
TBLPROPERTIES (delta.enableChangeDataFeed = true);

ALTER TABLE {product}.dbxlab_range SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {product}.range_evidence SET TAGS ('demo_data' = 'true', 'app' = 'quote_ai');
ALTER TABLE {ai}.sku_search_doc SET TAGS ('app' = 'quote_ai');
